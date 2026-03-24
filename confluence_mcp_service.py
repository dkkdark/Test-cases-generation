import json
import os
import re
import shlex
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html import unescape
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


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
        "CONFLUENCE_URL",
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_API_TOKEN",
        "CONFLUENCE_PERSONAL_TOKEN",
        "CONFLUENCE_USERNAME",
        "CONFLUENCE_EMAIL",
        "ATLASSIAN_URL",
        "ATLASSIAN_API_TOKEN",
        "ATLASSIAN_PERSONAL_TOKEN",
        "ATLASSIAN_EMAIL",
        "ATLASSIAN_USERNAME",
    ):
        value = os.getenv(key)
        if value:
            env[key] = value

    if "ATLASSIAN_URL" not in env and "CONFLUENCE_BASE_URL" in env:
        env["ATLASSIAN_URL"] = env["CONFLUENCE_BASE_URL"]
    if "CONFLUENCE_BASE_URL" not in env and "ATLASSIAN_URL" in env:
        env["CONFLUENCE_BASE_URL"] = env["ATLASSIAN_URL"]
    if "CONFLUENCE_URL" not in env and "CONFLUENCE_BASE_URL" in env:
        env["CONFLUENCE_URL"] = env["CONFLUENCE_BASE_URL"]
    if "CONFLUENCE_BASE_URL" not in env and "CONFLUENCE_URL" in env:
        env["CONFLUENCE_BASE_URL"] = env["CONFLUENCE_URL"]

    if "ATLASSIAN_API_TOKEN" not in env and "CONFLUENCE_API_TOKEN" in env:
        env["ATLASSIAN_API_TOKEN"] = env["CONFLUENCE_API_TOKEN"]
    if "CONFLUENCE_API_TOKEN" not in env and "ATLASSIAN_API_TOKEN" in env:
        env["CONFLUENCE_API_TOKEN"] = env["ATLASSIAN_API_TOKEN"]
    if "ATLASSIAN_PERSONAL_TOKEN" not in env and "CONFLUENCE_PERSONAL_TOKEN" in env:
        env["ATLASSIAN_PERSONAL_TOKEN"] = env["CONFLUENCE_PERSONAL_TOKEN"]
    if "CONFLUENCE_PERSONAL_TOKEN" not in env and "ATLASSIAN_PERSONAL_TOKEN" in env:
        env["CONFLUENCE_PERSONAL_TOKEN"] = env["ATLASSIAN_PERSONAL_TOKEN"]

    if "ATLASSIAN_EMAIL" not in env and "CONFLUENCE_EMAIL" in env:
        env["ATLASSIAN_EMAIL"] = env["CONFLUENCE_EMAIL"]
    if "CONFLUENCE_EMAIL" not in env and "ATLASSIAN_EMAIL" in env:
        env["CONFLUENCE_EMAIL"] = env["ATLASSIAN_EMAIL"]
    if "CONFLUENCE_USERNAME" not in env and "CONFLUENCE_EMAIL" in env:
        env["CONFLUENCE_USERNAME"] = env["CONFLUENCE_EMAIL"]
    if "CONFLUENCE_EMAIL" not in env and "CONFLUENCE_USERNAME" in env:
        env["CONFLUENCE_EMAIL"] = env["CONFLUENCE_USERNAME"]
    if "CONFLUENCE_USERNAME" not in env and "ATLASSIAN_USERNAME" in env:
        env["CONFLUENCE_USERNAME"] = env["ATLASSIAN_USERNAME"]

    if "ATLASSIAN_USERNAME" not in env and "ATLASSIAN_EMAIL" in env:
        env["ATLASSIAN_USERNAME"] = env["ATLASSIAN_EMAIL"]
    if "ATLASSIAN_USERNAME" not in env and "CONFLUENCE_USERNAME" in env:
        env["ATLASSIAN_USERNAME"] = env["CONFLUENCE_USERNAME"]

    return env


@dataclass(frozen=True)
class ConfluenceMCPServerConfig:
    command: str
    args: List[str]
    cwd: Optional[str]
    env: Dict[str, str]


class ConfluenceMCPClient:
    def __init__(self, config: Optional[ConfluenceMCPServerConfig] = None) -> None:
        self._config = config or self._load_config()

    def _load_config(self) -> ConfluenceMCPServerConfig:
        command = os.getenv("CONFLUENCE_MCP_COMMAND", "uvx")
        resolved = shutil.which(command)
        if resolved:
            command = resolved
        else:
            print(f"[Confluence MCP] WARNING: command '{command}' not found in PATH")
        args = _parse_args(os.getenv("CONFLUENCE_MCP_ARGS", '["mcp-atlassian"]'))
        cwd = os.getenv("CONFLUENCE_MCP_CWD")
        env = _build_env()
        return ConfluenceMCPServerConfig(command=command, args=args, cwd=cwd, env=env)

    @asynccontextmanager
    async def session(self) -> ClientSession:
        params = StdioServerParameters(
            command=self._config.command,
            args=self._config.args,
            env=self._config.env,
            cwd=self._config.cwd,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


class ConfluenceMCPToolResolver:
    def __init__(self, tools: Iterable[Any]) -> None:
        self._tools = list(tools)
        self._tools_by_name = {tool.name: tool for tool in self._tools}

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

    def page_fetch_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "confluence_get_page",
                "get_page",
                "get_confluence_page",
                "fetch_page",
                "read_page",
                "get_page_content",
                "confluence_get_page_content",
            ]
        ) or self._find_by_predicate(
            lambda tool: self._looks_like_page_fetch_tool(tool)
        )

    @staticmethod
    def _looks_like_page_fetch_tool(tool: Any) -> bool:
        description = (getattr(tool, "description", "") or "").lower()
        schema = getattr(tool, "inputSchema", {}) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        prop_names = {str(name).lower() for name in props.keys()}
        has_page_locator = bool(
            {"url", "link", "pageurl", "page_url", "pageid", "page_id", "content_id", "id"} & prop_names
        )
        mentions_page = "confluence" in description or "page" in description or "wiki" in description
        mentions_content = "content" in description or "document" in description or "read" in description
        return has_page_locator and (mentions_page or mentions_content)


class ConfluenceMCPService:
    def __init__(self) -> None:
        self._client = ConfluenceMCPClient()

    @staticmethod
    def _safe_serialize(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=lambda obj: getattr(obj, "__dict__", str(obj)))
        except Exception:
            return repr(value)

    @staticmethod
    def _object_to_mapping(payload: Any) -> Any:
        if isinstance(payload, (dict, list, str, int, float, bool)) or payload is None:
            return payload
        for method_name in ("model_dump", "dict"):
            method = getattr(payload, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    pass
        if hasattr(payload, "__dict__"):
            try:
                return vars(payload)
            except Exception:
                pass
        return payload

    def _collect_text_fragments(self, payload: Any, path: str = "") -> List[str]:
        payload = self._object_to_mapping(payload)

        if payload is None or isinstance(payload, bool):
            return []

        if isinstance(payload, str):
            stripped = payload.strip()
            if not stripped:
                return []
            if stripped.lower() in {"true", "false", "null", "none"}:
                return []
            try:
                parsed = json.loads(stripped)
                return self._collect_text_fragments(parsed, path)
            except Exception:
                if "<" in stripped and ">" in stripped:
                    stripped = self._strip_html(stripped)
                lowered_path = path.lower()
                if any(
                    token in lowered_path
                    for token in (
                        "download_url",
                        "media_type",
                        "file_size",
                        "id",
                        "version",
                        "status",
                        "type",
                    )
                ):
                    return []
                if stripped.startswith("http://") or stripped.startswith("https://") or stripped.startswith("/download/"):
                    return []
                return [stripped]

        if isinstance(payload, list):
            fragments: List[str] = []
            for index, item in enumerate(payload):
                fragments.extend(self._collect_text_fragments(item, f"{path}[{index}]"))
            return fragments

        if isinstance(payload, dict):
            fragments: List[str] = []
            for key, value in payload.items():
                key_path = f"{path}.{key}" if path else str(key)
                lowered_key = str(key).lower()
                if lowered_key in {"iserror", "is_error", "success", "ok", "_links"}:
                    continue
                fragments.extend(self._collect_text_fragments(value, key_path))
            return fragments

        return []

    @staticmethod
    def _extract_page_id(doc_url: str) -> Optional[str]:
        parsed = urlparse(doc_url)
        query_page_id = parse_qs(parsed.query).get("pageId")
        if query_page_id:
            return query_page_id[0]

        patterns = [
            r"/pages/(\d+)",
            r"/spaces/[^/]+/pages/(\d+)",
            r"/wiki/spaces/[^/]+/pages/(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, parsed.path)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _strip_html(raw_html: str) -> str:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(li|tr|h[1-6])\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def _extract_text(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, bool):
            return ""
        payload = self._object_to_mapping(payload)
        if isinstance(payload, bool):
            return ""
        if isinstance(payload, str):
            stripped = payload.strip()
            if not stripped:
                return ""
            try:
                return self._extract_text(json.loads(stripped))
            except Exception:
                if "<" in stripped and ">" in stripped:
                    return self._strip_html(stripped)
                return stripped
        if isinstance(payload, list):
            parts = [self._extract_text(item) for item in payload]
            return "\n\n".join([part for part in parts if part.strip()])
        if isinstance(payload, dict):
            text_parts = []
            priority_paths = [
                payload.get("markdown"),
                payload.get("body_markdown"),
                payload.get("content_markdown"),
                payload.get("page_content"),
                payload.get("body"),
                payload.get("content"),
                payload.get("structuredContent"),
                payload.get("structured_content"),
                payload.get("text"),
                payload.get("value"),
                payload.get("data"),
                payload.get("json"),
                payload.get("resource"),
                payload.get("contents"),
                payload.get("items"),
                ((payload.get("body") or {}).get("storage") or {}).get("value") if isinstance(payload.get("body"), dict) else None,
                ((payload.get("body") or {}).get("view") or {}).get("value") if isinstance(payload.get("body"), dict) else None,
                ((payload.get("page") or {}).get("body") or {}).get("value") if isinstance(payload.get("page"), dict) else None,
                ((payload.get("page") or {}).get("body") or {}).get("storage") if isinstance(payload.get("page"), dict) else None,
                ((payload.get("page") or {}).get("body") or {}).get("view") if isinstance(payload.get("page"), dict) else None,
            ]
            for candidate in priority_paths:
                text = self._extract_text(candidate)
                if text.strip():
                    text_parts.append(text)

            for key, value in payload.items():
                if key in {
                    "id",
                    "status",
                    "type",
                    "space",
                    "version",
                    "metadata",
                    "_links",
                    "isError",
                    "is_error",
                    "success",
                    "ok",
                }:
                    continue
                extracted = self._extract_text(value)
                if extracted.strip():
                    text_parts.append(extracted)

            deduped_parts = []
            seen = set()
            for part in text_parts:
                normalized = part.strip()
                if not normalized:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                deduped_parts.append(normalized)
            if deduped_parts:
                return "\n\n".join(deduped_parts)

            fallback_fragments = self._collect_text_fragments(payload)
            fallback_deduped = []
            fallback_seen = set()
            for fragment in fallback_fragments:
                normalized = fragment.strip()
                if not normalized or normalized in fallback_seen:
                    continue
                fallback_seen.add(normalized)
                fallback_deduped.append(normalized)
            return "\n\n".join(fallback_deduped)
        return str(payload)

    @staticmethod
    def _tool_args_variants(tool: Any, doc_url: str, page_id: Optional[str]) -> List[Dict[str, Any]]:
        schema = getattr(tool, "inputSchema", {}) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}

        url_keys = ["url", "link", "pageUrl", "page_url"]
        id_keys = ["page_id", "pageId", "contentId", "content_id", "id"]
        variants: List[Dict[str, Any]] = []

        for key in url_keys:
            if key in props:
                variants.append({key: doc_url})

        if page_id is not None:
            for key in id_keys:
                if key in props:
                    value: Any = int(page_id) if str(page_id).isdigit() else page_id
                    variant = {key: value}
                    if "convert_to_markdown" in props:
                        variant["convert_to_markdown"] = True
                    if "include_metadata" in props:
                        variant["include_metadata"] = True
                    variants.append(variant)

        if not variants:
            variants.append({"url": doc_url})
            if page_id is not None:
                value = int(page_id) if str(page_id).isdigit() else page_id
                variants.append(
                    {
                        "page_id": value,
                        "convert_to_markdown": True,
                        "include_metadata": True,
                    }
                )

        deduped: List[Dict[str, Any]] = []
        seen = set()
        for variant in variants:
            key = tuple(sorted(variant.items()))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(variant)
        return deduped

    async def fetch_page_content(self, doc_url: str) -> str:
        if not doc_url or not doc_url.strip():
            return ""

        page_id = self._extract_page_id(doc_url)
        attempted_args: List[Dict[str, Any]] = []
        async with self._client.session() as session:
            tools_result = await session.list_tools()
            resolver = ConfluenceMCPToolResolver(tools_result.tools)
            tool = resolver.page_fetch_tool()
            if tool is None:
                raise RuntimeError("Confluence MCP server does not expose a page fetch tool.")

            last_error = None
            for args in self._tool_args_variants(tool, doc_url.strip(), page_id):
                attempted_args.append(args)
                try:
                    result = await session.call_tool(tool.name, args)
                    text = self._extract_text(result)
                    if text.strip():
                        print(
                            "[Confluence MCP] page fetch success:"
                            f" tool={tool.name}"
                            f" args={args}"
                            f" extracted_len={len(text.strip())}"
                            f" preview={text.strip()[:250]!r}"
                        )
                        return text
                    print(
                        "[Confluence MCP] page fetch empty text:"
                        f" tool={tool.name}"
                        f" args={args}"
                        f" raw={self._safe_serialize(self._object_to_mapping(result))[:1200]}"
                    )
                except Exception as exc:
                    last_error = exc
                    continue

        if last_error:
            raise RuntimeError(
                f"Failed to fetch Confluence page via MCP: {last_error}. Attempted args: {attempted_args}"
            )
        raise RuntimeError(
            "Failed to fetch Confluence page via MCP. "
            f"Attempted args: {attempted_args}. "
            f"Result was empty or unsupported."
        )
