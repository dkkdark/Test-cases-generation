import asyncio
import json
import os
import shlex
import shutil
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from contextlib import asynccontextmanager
from datetime import timedelta

import requests
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


DEFAULT_RQL = None


def _parse_args(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            return shlex.split(raw)
    return shlex.split(raw)


def _build_env() -> Dict[str, str]:
    env: Dict[str, str] = dict(os.environ)
    for key in (
        "ALLURE_TESTOPS_URL",
        "ALLURE_TOKEN",
        "ALLURE_PROJECT_ID",
        "PROJECT_ID",
        "ALLURE_TREE_ID",
        "TREE_ID",
    ):
        value = os.getenv(key)
        if value:
            env[key] = value

    if "ALLURE_PROJECT_ID" not in env and "PROJECT_ID" in env:
        env["ALLURE_PROJECT_ID"] = env["PROJECT_ID"]
    if "PROJECT_ID" not in env and "ALLURE_PROJECT_ID" in env:
        env["PROJECT_ID"] = env["ALLURE_PROJECT_ID"]
    if "ALLURE_TREE_ID" not in env and "TREE_ID" in env:
        env["ALLURE_TREE_ID"] = env["TREE_ID"]
    if "TREE_ID" not in env and "ALLURE_TREE_ID" in env:
        env["TREE_ID"] = env["ALLURE_TREE_ID"]

    return env


@dataclass(frozen=True)
class AllureMCPServerConfig:
    command: str
    args: List[str]
    cwd: Optional[str]
    env: Dict[str, str]


class AllureMCPClient:
    def __init__(self, config: Optional[AllureMCPServerConfig] = None) -> None:
        self._config = config or self._load_config()
        self._read_timeout_seconds = self._load_timeout()

    def _load_config(self) -> AllureMCPServerConfig:
        command = os.getenv("ALLURE_MCP_COMMAND", "node")
        resolved = shutil.which(command)
        if resolved:
            command = resolved
        else:
            print(f"[MCP] WARNING: command '{command}' not found in PATH")
        args = _parse_args(os.getenv("ALLURE_MCP_ARGS", ""))
        cwd = os.getenv("ALLURE_MCP_CWD")
        env = _build_env()
        return AllureMCPServerConfig(command=command, args=args, cwd=cwd, env=env)

    def _load_timeout(self) -> Optional[int]:
        value = os.getenv("ALLURE_MCP_READ_TIMEOUT_SECONDS")
        if not value:
            return None
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except ValueError:
            return None

    @asynccontextmanager
    async def _session(self) -> ClientSession:
        print(
            "[MCP] stdio spawn:"
            f" command={self._config.command}"
            f" args={self._config.args}"
            f" cwd={self._config.cwd}"
        )
        params = StdioServerParameters(
            command=self._config.command,
            args=self._config.args,
            env=self._config.env,
            cwd=self._config.cwd,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            read_timeout = (
                timedelta(seconds=self._read_timeout_seconds)
                if self._read_timeout_seconds is not None
                else None
            )
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=read_timeout) as session:
                print("[MCP] session initialize: start")
                await session.initialize()
                print("[MCP] session initialize: done")
                yield session

    def with_session(self):
        return self._session()


class AllureMCPToolResolver:
    def __init__(self, tools: Iterable[Any]) -> None:
        self._tools = list(tools)
        self._tools_by_name = {t.name: t for t in self._tools}

    def _find_by_name(self, names: Iterable[str]) -> Optional[Any]:
        for name in names:
            if name in self._tools_by_name:
                return self._tools_by_name[name]
        return None

    def _find_by_predicate(self, predicate) -> Optional[Any]:
        for tool in self._tools:
            if predicate(tool):
                return tool
        return None

    def search_test_cases_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "search_test_cases",
                "allure_search_test_cases",
                "allure_search_1",
            ]
        ) or self._find_by_predicate(
            lambda t: "aql" in (t.description or "").lower() or "__search" in (t.description or "").lower()
        )

    def list_test_cases_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "list_test_cases",
                "allure_list_test_cases",
                "allure_findAll_12",
            ]
        ) or self._find_by_predicate(
            lambda t: "test case" in (t.description or "").lower()
            and "find all" in (t.description or "").lower()
        )

    def get_test_case_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "get_test_case",
                "allure_get_test_case",
                "allure_findOne_11",
            ]
        ) or self._find_by_predicate(
            lambda t: "test case" in (t.description or "").lower()
            and "by id" in (t.description or "").lower()
        )

    def create_test_case_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "create_test_case",
                "allure_create_test_case",
                "allure_create_14",
            ]
        ) or self._find_by_predicate(
            lambda t: "create" in (t.description or "").lower() and "test case" in (t.description or "").lower()
        )

    def get_scenario_tool(self) -> Optional[Any]:
        return self._find_by_predicate(
            lambda t: "scenario" in (t.description or "").lower()
            or "step" in (t.description or "").lower()
        )

    def create_step_tool(self) -> Optional[Any]:
        return self._find_by_predicate(
            lambda t: "step" in (t.description or "").lower()
            and "create" in (t.description or "").lower()
        )

    def get_test_case_custom_fields_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "get_test_case_custom_fields",
                "allure_get_test_case_custom_fields",
            ]
        ) or self._find_by_predicate(
            lambda t: "custom field" in (t.description or "").lower()
            and "test case" in (t.description or "").lower()
        )


class AllureMCPService:
    def __init__(self) -> None:
        self._client = AllureMCPClient()
        self._project_id = self._read_project_id()
        self._tree_id = self._read_tree_id()
        self._rql = os.getenv("ALLURE_RQL") or DEFAULT_RQL
        self._base_url = os.getenv("ALLURE_TESTOPS_URL") or os.getenv("BASE_URL", "")
        self._user_token = os.getenv("ALLURE_TOKEN") or os.getenv("USER_TOKEN", "")
        self._jwt_token: Optional[str] = None
        self._cfv_options_cache: Dict[int, List[Dict[str, Any]]] = {}

    def _read_project_id(self) -> Optional[int]:
        for key in ("ALLURE_PROJECT_ID", "PROJECT_ID"):
            value = os.getenv(key)
            if value:
                try:
                    return int(value)
                except ValueError:
                    return None
        return None


    def _read_tree_id(self) -> Optional[int]:
        for key in ("ALLURE_TREE_ID", "TREE_ID"):
            value = os.getenv(key)
            if value:
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _format_exception(exc: BaseException) -> str:
        exceptions = getattr(exc, "exceptions", None)
        if exceptions and isinstance(exceptions, list):
            return " | ".join([repr(item) for item in exceptions])
        return repr(exc)

    @staticmethod
    def _escape_rql_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _tool_requires_rql(tool: Any) -> bool:
        schema = getattr(tool, "inputSchema", {}) or {}
        if not isinstance(schema, dict):
            return False
        required = schema.get("required", []) or []
        return "rql" in required

    def _apply_scope_args(self, tool: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        schema = getattr(tool, "inputSchema", {}) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}

        if self._project_id is not None and "projectId" in props:
            args["projectId"] = self._project_id

        if self._tree_id is not None and "treeId" in props:
            args["treeId"] = self._tree_id

        if self._rql and "rql" in props:
            args["rql"] = self._rql

        return args

    def _pick_list_tool(self, resolver: "AllureMCPToolResolver") -> Any:
        candidates = [
            resolver.list_test_cases_tool(),
            resolver.search_test_cases_tool(),
        ]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return None
        if self._rql:
            for tool in candidates:
                schema = getattr(tool, "inputSchema", {}) or {}
                props = schema.get("properties", {}) if isinstance(schema, dict) else {}
                if "rql" in props:
                    return tool
        for tool in candidates:
            if not self._tool_requires_rql(tool):
                return tool
        return candidates[0]

    def _fetch_jwt(self) -> str:
        if self._jwt_token:
            return self._jwt_token
        url = f"{self._base_url}/api/uaa/oauth/token"
        resp = requests.post(
            url,
            headers={"Accept": "application/json"},
            data={"grant_type": "apitoken", "scope": "openid", "token": self._user_token},
            verify=False,
        )
        resp.raise_for_status()
        self._jwt_token = resp.json().get("access_token", "")
        return self._jwt_token

    def _api_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._fetch_jwt()}", "Accept": "application/json"}

    def fetch_fields_direct(self, testcase_id: int) -> List[Dict[str, Any]]:
        project_id = self._project_id or 2
        url = f"{self._base_url}/api/testcase/{testcase_id}/cfv?projectId={project_id}"
        resp = requests.get(url, headers=self._api_headers(), verify=False)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [
            {
                "fieldValue": field.get("name"),
                "fieldName": (field.get("customField") or {}).get("name"),
            }
            for field in data
            if field.get("name") and (field.get("customField") or {}).get("name")
        ]

    CF_ID_MAP = {
        "Product": 9,
        "Epic": -1,
        "Feature": -2,
        "Story": -3,
        "Component": -4,
        "Issue": 17,
    }

    PRODUCT_VALUE_ID = 10152

    def _fetch_cfv_options(self, custom_field_id: int) -> List[Dict[str, Any]]:
        if custom_field_id in self._cfv_options_cache:
            return self._cfv_options_cache[custom_field_id]
        project_id = self._project_id or 2
        url = f"{self._base_url}/api/project/{project_id}/cfv?customFieldId={custom_field_id}&size=350"
        resp = requests.get(url, headers=self._api_headers(), verify=False)
        resp.raise_for_status()
        data = resp.json()
        options = data.get("content", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        self._cfv_options_cache[custom_field_id] = options
        return options

    def _create_cfv_option(self, custom_field_id: int, field_value: str) -> Optional[int]:
        project_id = self._project_id or 2
        endpoints_and_payloads = [
            (
                f"{self._base_url}/api/project/{project_id}/cfv",
                {"name": field_value, "customFieldId": custom_field_id},
            ),
            (
                f"{self._base_url}/api/project/{project_id}/cfv",
                {"name": field_value, "customField": {"id": custom_field_id}},
            ),
            (
                f"{self._base_url}/api/cfv",
                {"name": field_value, "projectId": project_id, "customField": {"id": custom_field_id}},
            ),
        ]

        for url, payload in endpoints_and_payloads:
            try:
                resp = requests.post(url, headers=self._api_headers(), json=payload, verify=False)
                if not resp.ok:
                    print(f"[MCP] _create_cfv_option: failed url={url} status={resp.status_code} body={resp.text[:200]}")
                    continue
                data = resp.json() if resp.content else {}
                created_id = data.get("id") if isinstance(data, dict) else None
                if created_id is not None:
                    self._cfv_options_cache.pop(custom_field_id, None)
                    print(f"[MCP] _create_cfv_option: created '{field_value}' for cf={custom_field_id} id={created_id}")
                    return int(created_id)
            except Exception as exc:
                print(f"[MCP] _create_cfv_option: exception for url={url}: {exc}")

        return None

    def _resolve_custom_fields(self, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        resolved: List[Dict[str, Any]] = []
        resolved.append({"customField": {"id": self.CF_ID_MAP["Product"]}, "id": self.PRODUCT_VALUE_ID})

        fields_without_product = [f for f in fields if f.get("fieldName") != "Product"]
        for field in fields_without_product:
            field_name = field.get("fieldName")
            field_value = field.get("fieldValue")
            if not field_name or not field_value:
                continue
            cf_id = self.CF_ID_MAP.get(field_name)
            if cf_id is None:
                print(f"[MCP] _resolve_custom_fields: unknown field '{field_name}', skipping")
                continue
            options = self._fetch_cfv_options(cf_id)
            value_id = None
            for opt in options:
                if opt.get("name") == field_value:
                    value_id = opt.get("id")
                    break
            if value_id is None:
                print(f"[MCP] _resolve_custom_fields: value '{field_value}' not found for '{field_name}', trying to create")
                value_id = self._create_cfv_option(cf_id, str(field_value))
                if value_id is None:
                    print(f"[MCP] _resolve_custom_fields: failed to create value '{field_value}' for '{field_name}'")
                    continue
            resolved.append({"customField": {"id": cf_id}, "id": value_id})

        print(f"[MCP] _resolve_custom_fields: {resolved}")
        return resolved

    def _create_scenario_step_direct(self, text: str, testcase_id: int) -> bool:
        url = f"{self._base_url}/api/testcase/step"
        payload = {
            "bodyJson": self._build_body_json(text),
            "testCaseId": testcase_id,
        }
        resp = requests.post(url, headers=self._api_headers(), json=payload, verify=False)
        if resp.ok:
            return True
        print(f"[MCP] _create_scenario_step_direct: failed for tc={testcase_id}: {resp.status_code} {resp.text[:200]}")
        return False

    def _create_scenario_direct(self, steps: List[str], testcase_id: int) -> int:
        created = 0
        for step in steps or []:
            if not step:
                continue
            if self._create_scenario_step_direct(str(step), testcase_id):
                created += 1
        return created

    def _get_scenario_steps_with_ids_direct(self, testcase_id: int) -> List[Dict[str, Any]]:
        url = f"{self._base_url}/api/testcase/{testcase_id}/step"
        resp = requests.get(url, headers=self._api_headers(), verify=False)
        resp.raise_for_status()
        data = resp.json() or {}
        scenario_steps = data.get("scenarioSteps", {})
        if isinstance(scenario_steps, dict):
            return [
                {"id": step_id, "body": step.get("body", "")}
                for step_id, step in scenario_steps.items()
                if isinstance(step, dict)
            ]
        return []

    def _update_scenario_step_direct(self, step_id: int, text: str, testcase_id: int) -> bool:
        url = f"{self._base_url}/api/testcase/step/{step_id}"
        payload = {
            "bodyJson": self._build_body_json(text),
            "testCaseId": testcase_id,
        }
        resp = requests.put(url, headers=self._api_headers(), json=payload, verify=False)
        if resp.ok:
            return True
        print(f"[MCP] _update_scenario_step_direct: failed for step={step_id}: {resp.status_code} {resp.text[:200]}")
        return False

    def _delete_scenario_step_direct(self, step_id: int) -> bool:
        url = f"{self._base_url}/api/testcase/step/{step_id}"
        resp = requests.delete(url, headers=self._api_headers(), verify=False)
        if resp.ok:
            return True
        print(f"[MCP] _delete_scenario_step_direct: failed for step={step_id}: {resp.status_code} {resp.text[:200]}")
        return False

    def _replace_scenario_direct(self, steps: List[str], testcase_id: int) -> Dict[str, int]:
        existing_steps = self._get_scenario_steps_with_ids_direct(testcase_id)
        updated = 0
        created = 0
        deleted = 0

        for index, text in enumerate(steps or []):
            if not text:
                continue
            if index < len(existing_steps):
                step_id = existing_steps[index].get("id")
                if step_id is not None and self._update_scenario_step_direct(int(step_id), str(text), testcase_id):
                    updated += 1
            else:
                if self._create_scenario_step_direct(str(text), testcase_id):
                    created += 1

        for extra_step in existing_steps[len(steps or []):]:
            step_id = extra_step.get("id")
            if step_id is not None and self._delete_scenario_step_direct(int(step_id)):
                deleted += 1

        return {"updated": updated, "created": created, "deleted": deleted}

    def _run(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError("AllureMCPService sync methods cannot run inside an active event loop.")

    @staticmethod
    def _tool_args_with_id(tool: Any, test_case_id: int) -> Dict[str, Any]:
        schema = getattr(tool, "inputSchema", {}) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if "testCaseId" in props:
            return {"testCaseId": test_case_id}
        if "id" in props:
            return {"id": test_case_id}
        return {"testCaseId": test_case_id}

    @staticmethod
    def _build_body_json(text: str) -> Dict[str, Any]:
        return {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def _extract_json_from_result(result: Any) -> Any:
        if getattr(result, "structuredContent", None) is not None:
            return result.structuredContent
        content = getattr(result, "content", None) or []
        for item in content:
            text = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else None)
            if not text:
                continue
            try:
                return json.loads(text)
            except Exception:
                return text
        return None

    @staticmethod
    def _extract_steps(data: Any) -> List[str]:
        if data is None:
            return []
        if isinstance(data, dict):
            if "scenarioSteps" in data and isinstance(data["scenarioSteps"], dict):
                return [s.get("body") for s in data["scenarioSteps"].values() if s.get("body")]
            if "steps" in data and isinstance(data["steps"], list):
                return [s.get("body") or s.get("text") or str(s) for s in data["steps"]]
        if isinstance(data, list):
            return [item.get("body") or item.get("text") or str(item) for item in data]
        return []

    @staticmethod
    def _extract_fields(data: Any) -> List[Dict[str, Any]]:
        if data is None:
            return []
        items = []
        if isinstance(data, dict):
            if "content" in data and isinstance(data["content"], list):
                items = data["content"]
            else:
                items = data.get("items") if isinstance(data.get("items"), list) else []
        elif isinstance(data, list):
            items = data

        result: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            field_value = item.get("name") or item.get("fieldValue") or item.get("value")
            custom_field = item.get("customField") if isinstance(item.get("customField"), dict) else {}
            field_name = item.get("fieldName") or custom_field.get("name") or item.get("customFieldName")
            if field_name and field_value is not None:
                result.append({"fieldName": field_name, "fieldValue": field_value})
        return result

    async def _load_test_cases_async(self, size: int = 20) -> List[Dict[str, Any]]:
        async with self._client.with_session() as session:
            tools_result = await session.list_tools()
            resolver = AllureMCPToolResolver(tools_result.tools)
            search_tool = self._pick_list_tool(resolver)
            if search_tool is None:
                raise RuntimeError("Allure MCP server does not expose a test case list/search tool.")
            if self._tool_requires_rql(search_tool) and not self._rql:
                raise RuntimeError("Selected MCP tool requires non-empty rql, but ALLURE_RQL is not set.")

            args: Dict[str, Any] = {"page": 0, "size": size}
            args = self._apply_scope_args(search_tool, args)

            first = await session.call_tool(search_tool.name, args)
            if first.isError:
                raise RuntimeError(f"Allure MCP tool error: {first.content}")
            first_data = self._extract_json_from_result(first) or {}

            content = first_data.get("content") if isinstance(first_data, dict) else None
            if content is None and isinstance(first_data, list):
                content = first_data
            content = content or []

            total_pages = 1
            if isinstance(first_data, dict) and isinstance(first_data.get("totalPages"), int):
                total_pages = max(1, first_data.get("totalPages", 1))

            all_cases = list(content)

            for page in range(1, total_pages):
                args["page"] = page
                page_result = await session.call_tool(search_tool.name, args)
                if page_result.isError:
                    raise RuntimeError(f"Allure MCP tool error: {page_result.content}")
                page_data = self._extract_json_from_result(page_result) or {}
                page_content = page_data.get("content") if isinstance(page_data, dict) else None
                if page_content is None and isinstance(page_data, list):
                    page_content = page_data
                all_cases.extend(page_content or [])

            scenario_tool = resolver.get_scenario_tool()
            fields_tool = resolver.get_test_case_custom_fields_tool()

            enriched: List[Dict[str, Any]] = []
            for case in all_cases:
                if not isinstance(case, dict):
                    continue
                case_id = case.get("id")
                if case_id is None:
                    continue

                if scenario_tool is not None:
                    scenario_args = self._tool_args_with_id(scenario_tool, case_id)
                    scenario_res = await session.call_tool(scenario_tool.name, scenario_args)
                    if not scenario_res.isError:
                        scenario_data = self._extract_json_from_result(scenario_res)
                        case["steps"] = self._extract_steps(scenario_data)

                if fields_tool is not None:
                    fields_args = self._tool_args_with_id(fields_tool, case_id)
                    fields_res = await session.call_tool(fields_tool.name, fields_args)
                    if not fields_res.isError:
                        fields_data = self._extract_json_from_result(fields_res)
                        case["fields"] = self._extract_fields(fields_data)

                enriched.append(case)

            return enriched

    async def list_test_cases_for_index_async(self, size: int = 200) -> List[Dict[str, Any]]:
        async with self._client.with_session() as session:
            tools_result = await session.list_tools()
            resolver = AllureMCPToolResolver(tools_result.tools)
            search_tool = self._pick_list_tool(resolver)
            if search_tool is None:
                raise RuntimeError("Allure MCP server does not expose a test case list/search tool.")
            if self._tool_requires_rql(search_tool) and not self._rql:
                raise RuntimeError("Selected MCP tool requires non-empty rql, but ALLURE_RQL is not set.")

            args: Dict[str, Any] = {"page": 0, "size": size}
            args = self._apply_scope_args(search_tool, args)

            print(f"[MCP] list_test_cases_for_index: tool={search_tool.name} args={args}")
            try:
                first = await session.call_tool(search_tool.name, args)
            except Exception as exc:
                print(f"[MCP] list_test_cases_for_index: call_tool failed: {self._format_exception(exc)}")
                raise
            if first.isError:
                print(f"[MCP] list_test_cases_for_index: tool error content={first.content}")
                raise RuntimeError(f"Allure MCP tool error: {first.content}")
            first_data = self._extract_json_from_result(first) or {}
            if isinstance(first_data, dict):
                print(f"[MCP] list_test_cases_for_index: totalPages={first_data.get('totalPages')}, totalElements={first_data.get('totalElements')}, content_len={len(first_data.get('content', []) or [])}")
            else:
                print(f"[MCP] list_test_cases_for_index: first_data type={type(first_data).__name__}")

            content = first_data.get("content") if isinstance(first_data, dict) else None
            if content is None and isinstance(first_data, list):
                content = first_data
            content = content or []
            total_pages = 1
            if isinstance(first_data, dict) and isinstance(first_data.get("totalPages"), int):
                total_pages = max(1, first_data.get("totalPages", 1))

            all_cases = list(content)

            for page in range(1, total_pages):
                args["page"] = page
                page_result = await session.call_tool(search_tool.name, args)
                if page_result.isError:
                    raise RuntimeError(f"Allure MCP tool error: {page_result.content}")
                page_data = self._extract_json_from_result(page_result) or {}
                page_content = page_data.get("content") if isinstance(page_data, dict) else None
                if page_content is None and isinstance(page_data, list):
                    page_content = page_data
                all_cases.extend(page_content or [])

            print(f"[MCP] list_test_cases_for_index: total={len(all_cases)}")
            return all_cases

    async def get_test_cases_by_ids_async(self, ids: List[str | int]) -> List[Dict[str, Any]]:
        async with self._client.with_session() as session:
            tools_result = await session.list_tools()
            resolver = AllureMCPToolResolver(tools_result.tools)
            get_tool = resolver.get_test_case_tool()
            if get_tool is None:
                raise RuntimeError("Allure MCP server does not expose a get test case tool.")

            scenario_tool = resolver.get_scenario_tool()
            fields_tool = resolver.get_test_case_custom_fields_tool()

            results: List[Dict[str, Any]] = []
            for case_id in ids:
                int_id = int(case_id) if isinstance(case_id, str) else case_id
                args = self._tool_args_with_id(get_tool, int_id)
                print(f"[MCP] get_test_case_by_id: tool={get_tool.name} args={args}")
                case_res = await session.call_tool(get_tool.name, args)
                if case_res.isError:
                    print(f"[MCP] get_test_case_by_id: ERROR id={int_id} content={case_res.content}")
                    continue
                case_data = self._extract_json_from_result(case_res) or {}
                if not isinstance(case_data, dict):
                    print(f"[MCP] get_test_case_by_id: SKIP id={int_id} not a dict, type={type(case_data).__name__}")
                    continue
                print(f"[MCP] get_test_case_by_id: OK id={int_id} name='{case_data.get('name', '')[:80]}'")
                case_data["id"] = case_data.get("id", case_id)

                if scenario_tool is not None:
                    scenario_args = self._tool_args_with_id(scenario_tool, int_id)
                    scenario_res = await session.call_tool(scenario_tool.name, scenario_args)
                    if not scenario_res.isError:
                        scenario_data = self._extract_json_from_result(scenario_res)
                        case_data["steps"] = self._extract_steps(scenario_data)

                if fields_tool is not None:
                    fields_args = self._tool_args_with_id(fields_tool, int_id)
                    fields_res = await session.call_tool(fields_tool.name, fields_args)
                    if not fields_res.isError:
                        fields_data = self._extract_json_from_result(fields_res)
                        case_data["fields"] = self._extract_fields(fields_data)

                if not case_data.get("fields"):
                    try:
                        case_data["fields"] = self.fetch_fields_direct(int_id)
                        print(f"[MCP] get_test_case_by_id: fields via API id={int_id} count={len(case_data['fields'])}")
                    except Exception as exc:
                        print(f"[MCP] get_test_case_by_id: fields API error id={int_id}: {exc}")
                        case_data["fields"] = []

                results.append(case_data)

            return results

    def load_test_cases(self, size: int = 20) -> List[Dict[str, Any]]:
        return self._run(self._load_test_cases_async(size=size))

    async def load_test_cases_async(self, size: int = 20) -> List[Dict[str, Any]]:
        return await self._load_test_cases_async(size=size)

    async def _create_test_case_async(
        self,
        name: str,
        precondition: str,
        steps: List[str],
        expected_result: str,
        fields: List[Dict[str, Any]],
    ) -> Any:
        non_empty_fields = [f for f in (fields or []) if f.get("fieldValue")]
        custom_fields = self._resolve_custom_fields(non_empty_fields) if non_empty_fields else []

        payload: Dict[str, Any] = {
            "automated": False,
            "name": name,
            "precondition": precondition or "",
            "expectedResult": expected_result or "",
        }
        if self._project_id is not None:
            payload["projectId"] = self._project_id
        if custom_fields:
            payload["customFields"] = custom_fields

        url = f"{self._base_url}/api/testcase"
        print(f"[MCP] create_test_case: POST {url}")
        print(f"[MCP] create_test_case: payload={json.dumps(payload, ensure_ascii=False)}")
        resp = requests.post(url, headers=self._api_headers(), json=payload, verify=False)
        resp.raise_for_status()
        created = resp.json()
        print(f"[MCP] create_test_case: created id={created.get('id')}")

        test_case_id = created.get("id") if isinstance(created, dict) else None
        if test_case_id is not None and steps:
            count = self._create_scenario_direct(steps, test_case_id)
            print(f"[MCP] create_test_case: {count}/{len(steps)} steps created for tc={test_case_id}")

        return created

    async def _update_test_case_async(
        self,
        test_case_id: int,
        name: str,
        precondition: str,
        steps: List[str],
        expected_result: str,
        fields: List[Dict[str, Any]],
    ) -> Any:
        non_empty_fields = [f for f in (fields or []) if f.get("fieldValue")]
        custom_fields = self._resolve_custom_fields(non_empty_fields) if non_empty_fields else []

        payload: Dict[str, Any] = {
            "id": test_case_id,
            "automated": False,
            "name": name,
            "precondition": precondition or "",
            "expectedResult": expected_result or "",
        }
        if self._project_id is not None:
            payload["projectId"] = self._project_id
        if custom_fields:
            payload["customFields"] = custom_fields

        url = f"{self._base_url}/api/testcase/{test_case_id}"
        print(f"[MCP] update_test_case: PUT {url}")
        print(f"[MCP] update_test_case: payload={json.dumps(payload, ensure_ascii=False)}")
        resp = requests.put(url, headers=self._api_headers(), json=payload, verify=False)
        resp.raise_for_status()
        updated_case = resp.json()

        scenario_result = self._replace_scenario_direct(steps, test_case_id)
        print(
            "[MCP] update_test_case: scenario updated "
            f"updated={scenario_result['updated']} created={scenario_result['created']} deleted={scenario_result['deleted']}"
        )

        return updated_case

    def create_test_case(
        self,
        name: str,
        precondition: str,
        steps: List[str],
        expected_result: str,
        fields: List[Dict[str, Any]],
    ) -> Any:
        return self._run(
            self._create_test_case_async(
                name=name,
                precondition=precondition,
                steps=steps,
                expected_result=expected_result,
                fields=fields,
            )
        )

    async def create_test_case_async(
        self,
        name: str,
        precondition: str,
        steps: List[str],
        expected_result: str,
        fields: List[Dict[str, Any]],
    ) -> Any:
        return await self._create_test_case_async(
            name=name,
            precondition=precondition,
            steps=steps,
            expected_result=expected_result,
            fields=fields,
        )

    def update_test_case(
        self,
        test_case_id: int,
        name: str,
        precondition: str,
        steps: List[str],
        expected_result: str,
        fields: List[Dict[str, Any]],
    ) -> Any:
        return self._run(
            self._update_test_case_async(
                test_case_id=test_case_id,
                name=name,
                precondition=precondition,
                steps=steps,
                expected_result=expected_result,
                fields=fields,
            )
        )

    async def update_test_case_async(
        self,
        test_case_id: int,
        name: str,
        precondition: str,
        steps: List[str],
        expected_result: str,
        fields: List[Dict[str, Any]],
    ) -> Any:
        return await self._update_test_case_async(
            test_case_id=test_case_id,
            name=name,
            precondition=precondition,
            steps=steps,
            expected_result=expected_result,
            fields=fields,
        )
