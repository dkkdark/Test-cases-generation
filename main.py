import json
import os
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from mcp.server.fastmcp import FastMCP
from allure_mcp_service import AllureMCPService
from confluence_mcp_service import ConfluenceMCPService
from prompts import get_test_case_prompt
from config import Config
from fts_index import TestCaseFTSIndex
from collections import Counter, defaultdict
from contextlib import asynccontextmanager

load_dotenv(override=True)


class TestCaseService:

    def __init__(self):
        self.mcp_service = AllureMCPService()
        self.confluence_service = ConfluenceMCPService()
        provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
        if provider == "ollama":
            self.model = ChatOllama(
                model=os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud"),
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            )
        elif provider == "anthropic":
            self.model = ChatAnthropic(
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                api_key=os.getenv("ANTHROPIC_API_KEY"),
            )
        else:
            self.model = ChatOpenAI(
                model='gpt-4o-mini',
                api_key=os.getenv("OPENAI_API")
            )
        self.prompt = ChatPromptTemplate.from_template(get_test_case_prompt())
        self.chain = self.prompt | self.model
        self.fts_index = TestCaseFTSIndex()

    @staticmethod
    def _extract_json_payload(raw_result: str):
        if not isinstance(raw_result, str):
            return raw_result
        text = raw_result.strip()
        if not text:
            return []
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            return json.loads(text)
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if not match:
            raise json.JSONDecodeError("No JSON found", raw_result, 0)
        return json.loads(match.group(1))

    @staticmethod
    def _normalize_fields_value(raw_fields) -> list[dict]:
        normalized = []
        if isinstance(raw_fields, dict):
            raw_fields = [{"fieldName": key, "fieldValue": value} for key, value in raw_fields.items()]

        if isinstance(raw_fields, str):
            pairs = [part.strip() for part in raw_fields.split(";") if part.strip()]
            parsed = []
            for pair in pairs:
                if ":" not in pair:
                    continue
                name, value = pair.split(":", 1)
                parsed.append({"fieldName": name.strip(), "fieldValue": value.strip()})
            raw_fields = parsed

        if not isinstance(raw_fields, list):
            return normalized

        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            field_name = item.get("fieldName") or item.get("name")
            field_value = item.get("fieldValue") or item.get("value")

            if not field_name and len(item) == 1:
                field_name, field_value = next(iter(item.items()))

            if field_name and field_value not in (None, ""):
                normalized.append({"fieldName": str(field_name), "fieldValue": str(field_value)})

        deduped = []
        seen = set()
        for field in normalized:
            key = (field["fieldName"], field["fieldValue"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(field)
        return deduped

    @staticmethod
    def _normalize_case_item(case: dict) -> dict:
        if not isinstance(case, dict):
            return {}
        return {
            "Operation": str(case.get("Operation", case.get("operation", "create"))).lower(),
            "Existing ID": case.get("Existing ID", case.get("existing_id")),
            "Change summary": case.get("Change summary", case.get("change_summary", "")),
            "Name": case.get("Name", case.get("name", "")),
            "Precondition": case.get("Precondition", case.get("precondition", "")),
            "Step": case.get("Step", case.get("steps", [])) if isinstance(case.get("Step", case.get("steps", [])), list) else [],
            "Expected result": case.get("Expected result", case.get("expected_result", "")),
            "Fields": TestCaseService._normalize_fields_value(case.get("Fields", case.get("fields", []))),
        }

    @staticmethod
    def _collect_shared_fields(grouped_cases: dict, inferred_fields: list[dict]) -> list[dict]:
        ordered = []
        seen_names = set()

        for field in inferred_fields or []:
            name = field.get("fieldName")
            value = field.get("fieldValue")
            if not name or value in (None, "") or name in seen_names:
                continue
            ordered.append({"fieldName": name, "fieldValue": value})
            seen_names.add(name)

        for items in grouped_cases.values():
            for case in items:
                for field in case.get("Fields", []):
                    name = field.get("fieldName")
                    value = field.get("fieldValue")
                    if not name or value in (None, "") or name in seen_names:
                        continue
                    ordered.append({"fieldName": name, "fieldValue": value})
                    seen_names.add(name)

        return ordered

    @staticmethod
    def _merge_case_fields(case_fields: list[dict], shared_fields: list[dict]) -> list[dict]:
        merged = {field["fieldName"]: dict(field) for field in shared_fields if field.get("fieldName")}
        for field in case_fields or []:
            name = field.get("fieldName")
            value = field.get("fieldValue")
            if not name or value in (None, ""):
                continue
            merged[name] = {"fieldName": name, "fieldValue": value}
        return list(merged.values())

    def _normalize_llm_result(self, raw_result: str, inferred_fields: list[dict]) -> dict:
        parsed = self._extract_json_payload(raw_result)
        if isinstance(parsed, list):
            parsed = {
                "most_important": parsed,
                "less_important": [],
                "possibly_affected_existing": [],
            }
        if not isinstance(parsed, dict):
            parsed = {}

        grouped = {}
        for key in ("most_important", "less_important", "possibly_affected_existing"):
            items = parsed.get(key, [])
            if not isinstance(items, list):
                items = []
            grouped[key] = [self._normalize_case_item(item) for item in items]

        shared_fields = self._collect_shared_fields(grouped, inferred_fields)
        for key, items in grouped.items():
            for item in items:
                if key == "possibly_affected_existing":
                    item["Operation"] = "update"
                item["Fields"] = self._merge_case_fields(item.get("Fields", []), shared_fields)

        return grouped

    @staticmethod
    def _format_case_for_prompt(case: dict) -> str:
        fields = case.get("fields", [])
        field_dict = {}
        if isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict):
                    field_name = item.get("fieldName", "")
                    field_value = item.get("fieldValue", "")
                    if field_name and field_value:
                        field_dict[field_name] = field_value
            formatted_fields = "; ".join(f"{name}: {value}" for name, value in field_dict.items())
        else:
            formatted_fields = str(fields)
            field_dict = fields if isinstance(fields, dict) else {}

        product = field_dict.get("Product", "")
        epic = field_dict.get("Epic", "")
        feature = field_dict.get("Feature", "")
        component = field_dict.get("Component", "")

        signature_parts = []
        if product:
            signature_parts.append(f"Product: {product}")
        if epic:
            signature_parts.append(f"Epic: {epic}")
        if feature:
            signature_parts.append(f"Feature: {feature}")
        if component:
            signature_parts.append(f"Component: {component}")

        signature = " | ".join(signature_parts) if signature_parts else ""

        steps = case.get("steps", [])
        if isinstance(steps, list):
            steps_text = "; ".join([str(s) for s in steps if s])
        else:
            steps_text = str(steps)

        return (
            f"ID: {case.get('id', '')}\n"
            f"{signature}\n"
            f"Fields: {formatted_fields}\n"
            f"Name: {case.get('name', '')}\n"
            f"Precondition: {case.get('precondition', '')}\n"
            f"Steps: {steps_text}\n"
            f"Expected result: {case.get('expectedResult', case.get('expected_result', ''))}\n"
        )

    @staticmethod
    def _format_search_candidates_for_prompt(candidates: list[dict]) -> str:
        if not candidates:
            return "Кандидаты не найдены."

        lines = []
        for idx, item in enumerate(candidates, start=1):
            label = "LIKELY_UPDATE" if item.get("update_candidate") else "RELATED"
            lines.append(
                f"{idx}. [{label}] ID={item.get('id')} "
                f"score={item.get('score', 0):.2f} "
                f"name_cov={item.get('name_coverage', 0):.2f} "
                f"steps_cov={item.get('steps_coverage', 0):.2f} "
                f"full_cov={item.get('full_coverage', 0):.2f} "
                f"matched_by={item.get('matched_by', '')}"
            )
        return "\n".join(lines)

    def _infer_fields_from_mcp_cases(self, cases: list[dict]) -> list[dict]:
        field_values_by_name = defaultdict(list)
        for case in cases or []:
            fields = case.get("fields", [])
            if isinstance(fields, list):
                for item in fields:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("fieldName")
                    value = item.get("fieldValue")
                    if name and value:
                        field_values_by_name[name].append(value)

        inferred = []
        for name, values in field_values_by_name.items():
            if not values:
                continue
            most_common_value = Counter(values).most_common(1)[0][0]
            inferred.append({"fieldName": name, "fieldValue": most_common_value})

        order = {"Product": 0, "Epic": 1, "Feature": 2, "Component": 3, "Story": 4}
        return sorted(inferred, key=lambda x: order.get(x["fieldName"], 999))

    @staticmethod
    def _truncate_context(text: str, limit: int = 12000) -> str:
        if not text:
            return ""
        normalized = text.strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "\n...[truncated]"

    @staticmethod
    def _extract_title_from_context(text: str) -> str:
        if not text:
            return ""
        for line in text.splitlines():
            normalized = line.strip().strip("#").strip()
            if len(normalized) >= 4:
                return normalized[:200]
        return ""

    @staticmethod
    def _build_search_query(query: str, confluence_context: str) -> str:
        base_query = (query or "").strip()
        if base_query:
            return base_query

        if not confluence_context:
            return ""

        title = TestCaseService._extract_title_from_context(confluence_context)
        paragraphs = []
        for chunk in re.split(r"\n\s*\n", confluence_context):
            normalized = " ".join(chunk.split())
            if len(normalized) < 20:
                continue
            paragraphs.append(normalized)
            if len(paragraphs) >= 3:
                break

        parts = []
        if title:
            parts.append(title)
        parts.extend(paragraphs)
        search_basis = " ".join(parts).strip()
        return search_basis[:600]

    async def get_test_case(self, query: str, doc_url: str = "", size: int = 10) -> str:
        try:
            print(f"[MCP] get_test_case: query='{query}' doc_url='{doc_url}' size={size}")
            confluence_context = ""
            if doc_url and doc_url.strip():
                confluence_context = self._truncate_context(
                    await self.confluence_service.fetch_page_content(doc_url.strip())
                )
                print(f"[MCP] get_test_case: loaded Confluence context size={len(confluence_context)}")

            search_query = self._build_search_query(query, confluence_context)
            print(f"[MCP] get_test_case: search_query='{search_query[:250]}'")
            search_limit = max(size, 12)
            search_candidates = self.fts_index.search_detailed(search_query, limit=search_limit)
            ids = [item["id"] for item in search_candidates]
            print(f"[MCP] get_test_case: ranked candidates={search_candidates}")
            if not ids:
                print("[MCP] get_test_case: FTS index returned no ids")
            cases = await self.mcp_service.get_test_cases_by_ids_async(ids[:search_limit])
            print(f"[MCP] get_test_case: found {len(cases)} cases")
            inferred_fields = self._infer_fields_from_mcp_cases(cases)
            print(f"[MCP] get_test_case: inferred fields {inferred_fields}")
            context = [self._format_case_for_prompt(case) for case in cases]
            ranked_context = self._format_search_candidates_for_prompt(search_candidates)
            effective_query = query.strip() if query and query.strip() else ""
            if not effective_query and confluence_context:
                effective_query = (
                    "Сгенерируй тест-кейсы по содержимому страницы Confluence. "
                    "Определи ключевые сценарии, которые нужно создать или обновить."
                )
            raw_result = self.chain.invoke(
                {
                    "query": effective_query,
                    "doc_url": doc_url.strip(),
                    "confluence_context": confluence_context,
                    "data": context,
                    "fields": inferred_fields,
                    "ranked_candidates": ranked_context,
                }
            ).content
            normalized_result = self._normalize_llm_result(raw_result, inferred_fields)
            print("[MCP] get_test_case: LLM response generated")
            return json.dumps(normalized_result, ensure_ascii=False)
        except Exception as e:
            exceptions = getattr(e, "exceptions", None)
            if exceptions and isinstance(exceptions, list):
                print(f"[MCP] get_test_case: error group {exceptions}")
            print(f"[MCP] get_test_case: error {e!r}")
            return f"Error: {str(e)}"

    async def rebuild_fts_index(self, size: int = 200) -> str:
        try:
            cases = await self.mcp_service.list_test_cases_for_index_async(size=size)
            count = self.fts_index.rebuild(cases)
            return f"FTS index rebuilt: {count} cases"
        except Exception as e:
            exceptions = getattr(e, "exceptions", None)
            if exceptions and isinstance(exceptions, list):
                print(f"[MCP] rebuild_fts_index: error group {exceptions}")
            print(f"[MCP] rebuild_fts_index: error {e!r}")
            return f"Error: {str(e)}"

    async def create_test_case(self, name: str, precondition: str, steps: list[str], expected_result: str, fields: list[dict]) -> str:
        try:
            created = await self.mcp_service.create_test_case_async(
                name=name,
                precondition=precondition,
                steps=steps,
                expected_result=expected_result,
                fields=fields,
            )
            created_id = created.get("id") if isinstance(created, dict) else None
            return f"Test case was successfully created! ID={created_id}" if created_id else "Test case was successfully created!"
        except BaseException as e:
            sub_exceptions = getattr(e, "exceptions", None)
            if sub_exceptions:
                for i, sub in enumerate(sub_exceptions):
                    print(f"[MCP] create_test_case: sub-exception[{i}]: {sub!r}")
            print(f"Error creating test case: {e!r}")
            import traceback
            traceback.print_exc()
            return f"Error: {str(e)}"

    async def update_test_case(self, test_case_id: int, name: str, precondition: str, steps: list[str], expected_result: str, fields: list[dict]) -> str:
        try:
            await self.mcp_service.update_test_case_async(
                test_case_id=test_case_id,
                name=name,
                precondition=precondition,
                steps=steps,
                expected_result=expected_result,
                fields=fields,
            )
            return f"Test case {test_case_id} was successfully updated!"
        except BaseException as e:
            sub_exceptions = getattr(e, "exceptions", None)
            if sub_exceptions:
                for i, sub in enumerate(sub_exceptions):
                    print(f"[MCP] update_test_case: sub-exception[{i}]: {sub!r}")
            print(f"Error updating test case: {e!r}")
            import traceback
            traceback.print_exc()
            return f"Error: {str(e)}"


mcp = FastMCP("Create TestCases")
test_case_service = TestCaseService()


@mcp.tool()
async def get_test_case(query: str, doc_url: str = "") -> str:
    return await test_case_service.get_test_case(query, doc_url=doc_url)

@mcp.tool()
async def rebuild_fts_index(size: int = 200) -> str:
    return await test_case_service.rebuild_fts_index(size=size)

@mcp.tool()
async def create_test_case(name: str, precondition: str, steps: list[str], expected_result: str, fields: list[dict]) -> str:
    return await test_case_service.create_test_case(
        name=name,
        precondition=precondition,
        steps=steps,
        expected_result=expected_result,
        fields=fields,
    )

@mcp.tool()
async def update_test_case(test_case_id: int, name: str, precondition: str, steps: list[str], expected_result: str, fields: list[dict]) -> str:
    return await test_case_service.update_test_case(
        test_case_id=test_case_id,
        name=name,
        precondition=precondition,
        steps=steps,
        expected_result=expected_result,
        fields=fields,
    )

@asynccontextmanager
async def lifespan(application: FastAPI):
    print("[MCP] startup: rebuild FTS index")
    result = await test_case_service.rebuild_fts_index(size=200)
    print(f"[MCP] startup: {result}")
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/get_test_case")
async def http_get_test_case(request: Request):
    data = await request.json()
    query_text = data.get("query", "")
    doc_url = data.get("doc_url", "")
    print(f"[HTTP] /get_test_case query='{query_text}' doc_url='{doc_url}'")
    result = await test_case_service.get_test_case(query_text, doc_url=doc_url)
    return {"result": result}

@app.post("/rebuild_fts_index")
async def http_rebuild_fts_index(request: Request):
    data = await request.json()
    size = data.get("size", 200)
    result = await test_case_service.rebuild_fts_index(size=size)
    return {"result": result}

@app.post("/create_test_case")
async def http_create_test_case(request: Request):
    data = await request.json()
    result = await test_case_service.create_test_case(
        name=data.get("name", ""),
        precondition=data.get("precondition", ""),
        steps=data.get("steps", []),
        expected_result=data.get("expected_result", ""),
        fields=data.get("fields", []),
    )
    return {"result": result}

@app.post("/update_test_case")
async def http_update_test_case(request: Request):
    data = await request.json()
    result = await test_case_service.update_test_case(
        test_case_id=int(data.get("test_case_id", 0)),
        name=data.get("name", ""),
        precondition=data.get("precondition", ""),
        steps=data.get("steps", []),
        expected_result=data.get("expected_result", ""),
        fields=data.get("fields", []),
    )
    return {"result": result}


if __name__ == "__main__":
    uvicorn.run("main:app", host=Config.Server.HOST, port=Config.Server.PORT, reload=False)
