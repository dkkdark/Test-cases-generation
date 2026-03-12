import json
import os
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
from prompts import get_test_case_prompt
from config import Config
from fts_index import TestCaseFTSIndex
from collections import Counter, defaultdict
from contextlib import asynccontextmanager

load_dotenv(override=True)


class TestCaseService:

    def __init__(self):
        self.mcp_service = AllureMCPService()
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
            f"{signature}\n"
            f"Fields: {formatted_fields}\n"
            f"Name: {case.get('name', '')}\n"
            f"Precondition: {case.get('precondition', '')}\n"
            f"Steps: {steps_text}\n"
            f"Expected result: {case.get('expectedResult', case.get('expected_result', ''))}\n"
        )

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

    async def get_test_case(self, query: str, size: int = 10) -> str:
        try:
            print(f"[MCP] get_test_case: query='{query}' size={size}")
            ids = self.fts_index.search(query, limit=size)
            print(f"[MCP] get_test_case: fts ids={ids}")
            if not ids:
                print("[MCP] get_test_case: FTS index returned no ids")
            cases = await self.mcp_service.get_test_cases_by_ids_async(ids)
            print(f"[MCP] get_test_case: found {len(cases)} cases")
            inferred_fields = self._infer_fields_from_mcp_cases(cases)
            print(f"[MCP] get_test_case: inferred fields {inferred_fields}")
            context = [self._format_case_for_prompt(case) for case in cases]
            result = self.chain.invoke({"query": query, "data": context, "fields": inferred_fields}).content
            print("[MCP] get_test_case: LLM response generated")
            return result
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
            await self.mcp_service.create_test_case_async(
                name=name,
                precondition=precondition,
                steps=steps,
                expected_result=expected_result,
                fields=fields,
            )
            return "Test case was successfully created!"
        except Exception as e:
            print(f"Error creating test case: {e}")
            return f"Error: {str(e)}"


mcp = FastMCP("Create TestCases")
test_case_service = TestCaseService()


@mcp.tool()
async def get_test_case(query: str) -> str:
    return await test_case_service.get_test_case(query)

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
    print(f"[HTTP] /get_test_case query='{query_text}'")
    result = await test_case_service.get_test_case(query_text)
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


if __name__ == "__main__":
    uvicorn.run("main:app", host=Config.Server.HOST, port=Config.Server.PORT, reload=False)
