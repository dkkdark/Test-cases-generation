import json
import os
import re
import shlex
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

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


@dataclass(frozen=True)
class BitbucketMCPServerConfig:
    command: str
    args: List[str]
    cwd: Optional[str]
    env: Dict[str, str]


class BitbucketMCPClient:
    def __init__(self, config: Optional[BitbucketMCPServerConfig] = None) -> None:
        self._config = config or self._load_config()

    def _load_config(self) -> BitbucketMCPServerConfig:
        command = os.getenv("BITBUCKET_MCP_COMMAND", "npx")
        resolved = shutil.which(command)
        if resolved:
            command = resolved
        else:
            print(f"[Bitbucket MCP] WARNING: command '{command}' not found in PATH")

        args = _parse_args(os.getenv("BITBUCKET_MCP_ARGS", '["bitbucket-mcp"]'))
        cwd = os.getenv("BITBUCKET_MCP_CWD")
        env = dict(os.environ)
        return BitbucketMCPServerConfig(command=command, args=args, cwd=cwd, env=env)

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


class BitbucketMCPToolResolver:
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

    def repo_tree_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "get_repository_tree",
                "list_repository_files",
                "get_repository_files",
                "repository_tree",
                "repo_tree",
                "list_source",
            ]
        ) or self._find_by_predicate(lambda tool: self._looks_like_repo_tree_tool(tool))

    def file_content_tool(self) -> Optional[Any]:
        return self._find_by_name(
            [
                "get_file_contents",
                "get_file_content",
                "read_file",
                "get_source_file",
                "get_repository_file",
                "get_file",
            ]
        ) or self._find_by_predicate(lambda tool: self._looks_like_file_tool(tool))

    @staticmethod
    def _looks_like_repo_tree_tool(tool: Any) -> bool:
        description = (getattr(tool, "description", "") or "").lower()
        schema = getattr(tool, "inputSchema", {}) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        prop_names = {str(name).lower() for name in props.keys()}
        has_repo_locator = bool({"workspace", "repo_slug", "repository", "repo", "repo_url", "url"} & prop_names)
        mentions_tree = any(token in description for token in ("tree", "files", "source", "repository contents"))
        return has_repo_locator and mentions_tree

    @staticmethod
    def _looks_like_file_tool(tool: Any) -> bool:
        description = (getattr(tool, "description", "") or "").lower()
        schema = getattr(tool, "inputSchema", {}) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        prop_names = {str(name).lower() for name in props.keys()}
        has_path = bool({"path", "file_path", "filepath"} & prop_names)
        has_repo_locator = bool({"workspace", "repo_slug", "repository", "repo", "repo_url", "url"} & prop_names)
        mentions_file = any(token in description for token in ("file", "source", "contents", "content", "read"))
        return has_path and has_repo_locator and mentions_file


class BitbucketMCPService:
    TEXT_EXTENSIONS = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rb", ".php",
        ".cs", ".swift", ".scala", ".sql", ".yml", ".yaml", ".json", ".xml", ".md",
        ".txt", ".graphql", ".proto", ".sh", ".env", ".ini", ".toml",
    }
    IGNORED_DIRS = {
        ".git", "node_modules", ".next", "dist", "build", "target", "coverage",
        "__pycache__", ".idea", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    }

    def __init__(self) -> None:
        self._client = BitbucketMCPClient()

    def _extract_terms(self, search_text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-zА-Яа-я0-9_./:-]{3,}", search_text or "")
        stop_words = {
            "что", "как", "для", "это", "или", "при", "если", "есть", "надо",
            "задача", "тест", "тесты", "кейсы", "создать", "обновить", "user",
            "story", "task", "jira", "issue", "with", "from", "into", "repo",
            "project", "code", "bitbucket", "branch", "description",
        }
        unique = []
        seen = set()
        for token in tokens:
            normalized = token.lower()
            if normalized in stop_words or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique[:20]

    @staticmethod
    def _parse_repo_ref(repo_url: str) -> Optional[dict]:
        parsed = urlparse(repo_url)
        if "bitbucket" not in (parsed.netloc or "").lower():
            return None

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return None

        repo_slug = parts[1]
        if repo_slug.endswith(".git"):
            repo_slug = repo_slug[:-4]

        return {"workspace": parts[0], "repo_slug": repo_slug, "repo_url": repo_url}

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

    def _collect_text_fragments(self, payload: Any) -> List[str]:
        payload = self._object_to_mapping(payload)
        if payload is None or isinstance(payload, bool):
            return []
        if isinstance(payload, str):
            stripped = payload.strip()
            if not stripped:
                return []
            return [stripped]
        if isinstance(payload, list):
            fragments: List[str] = []
            for item in payload:
                fragments.extend(self._collect_text_fragments(item))
            return fragments
        if isinstance(payload, dict):
            fragments: List[str] = []
            for key, value in payload.items():
                if str(key).lower() in {"type", "links", "_links"}:
                    continue
                fragments.extend(self._collect_text_fragments(value))
            return fragments
        return [str(payload)]

    def _extract_paths(self, payload: Any) -> list[str]:
        payload = self._object_to_mapping(payload)
        candidates = []

        if isinstance(payload, list):
            for item in payload:
                candidates.extend(self._extract_paths(item))
        elif isinstance(payload, dict):
            for key, value in payload.items():
                lowered = str(key).lower()
                if lowered in {"path", "file_path", "filepath"} and isinstance(value, str):
                    candidates.append(value)
                else:
                    candidates.extend(self._extract_paths(value))
        elif isinstance(payload, str):
            for line in payload.splitlines():
                line = line.strip().strip('"').strip("'")
                if "/" in line or Path(line).suffix:
                    candidates.append(line)

        deduped = []
        seen = set()
        for item in candidates:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _extract_text(self, payload: Any) -> str:
        fragments = self._collect_text_fragments(payload)
        if not fragments:
            return ""
        deduped = []
        seen = set()
        for fragment in fragments:
            normalized = fragment.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return "\n\n".join(deduped)

    @staticmethod
    def _is_supported_file(file_path: str) -> bool:
        path = Path(file_path)
        if any(part in BitbucketMCPService.IGNORED_DIRS for part in path.parts):
            return False
        return path.suffix.lower() in BitbucketMCPService.TEXT_EXTENSIONS or path.name in {"Dockerfile", "Makefile"}

    @staticmethod
    def _score_file(file_path: str, terms: list[str]) -> int:
        normalized_path = file_path.lower()
        score = max(1, 20 - normalized_path.count("/")) if not terms else 0
        for term in terms:
            if term in normalized_path:
                score += 8
        return score

    @staticmethod
    def _extract_snippet(text: str, terms: list[str], max_chars: int = 2500) -> str:
        lines = (text or "").splitlines()
        if not lines:
            return ""

        interesting_indexes = []
        lowered_lines = [line.lower() for line in lines]
        for idx, line in enumerate(lowered_lines):
            if any(term in line for term in terms):
                interesting_indexes.append(idx)

        if not interesting_indexes:
            return "\n".join(lines[: min(80, len(lines))])[:max_chars]

        selected = []
        seen = set()
        for idx in interesting_indexes[:6]:
            start = max(0, idx - 4)
            end = min(len(lines), idx + 5)
            for line_no in range(start, end):
                if line_no in seen:
                    continue
                seen.add(line_no)
                selected.append(f"{line_no + 1}: {lines[line_no]}")
        return "\n".join(selected)[:max_chars]

    def _tool_args_variants(self, tool: Any, repo_ref: dict, branch: str, file_path: Optional[str] = None) -> list[dict]:
        schema = getattr(tool, "inputSchema", {}) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        variants = []

        base_candidates = [
            {"workspace": repo_ref["workspace"], "repo_slug": repo_ref["repo_slug"]},
            {"workspace": repo_ref["workspace"], "repository": repo_ref["repo_slug"]},
            {"workspace": repo_ref["workspace"], "repo": repo_ref["repo_slug"]},
            {"repo_url": repo_ref["repo_url"]},
            {"url": repo_ref["repo_url"]},
        ]

        for candidate in base_candidates:
            filtered = {key: value for key, value in candidate.items() if key in props or not props}
            if filtered:
                variants.append(filtered)

        if not variants:
            variants.append({"repo_url": repo_ref["repo_url"]})

        if branch:
            branch_keys = ["branch", "ref", "revision"]
            branch_variants = []
            for variant in variants:
                extended = dict(variant)
                for key in branch_keys:
                    if key in props:
                        extended[key] = branch
                branch_variants.append(extended)
            variants.extend(branch_variants)

        if file_path is not None:
            path_keys = ["path", "file_path", "filepath"]
            file_variants = []
            for variant in variants:
                extended = dict(variant)
                for key in path_keys:
                    if key in props:
                        extended[key] = file_path
                file_variants.append(extended)
            variants = file_variants or variants

        deduped = []
        seen = set()
        for variant in variants:
            key = tuple(sorted(variant.items()))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(variant)
        return deduped

    async def collect_context(self, repo_url: str, branch: str, search_text: str, max_files: int = 6) -> str:
        if not repo_url.strip():
            return ""

        repo_ref = self._parse_repo_ref(repo_url.strip())
        if not repo_ref:
            return f"Bitbucket repository URL is invalid: {repo_url}"

        terms = self._extract_terms(search_text)
        async with self._client.session() as session:
            tools_result = await session.list_tools()
            resolver = BitbucketMCPToolResolver(tools_result.tools)
            tree_tool = resolver.repo_tree_tool()
            file_tool = resolver.file_content_tool()

            if tree_tool is None or file_tool is None:
                raise RuntimeError(
                    "Bitbucket MCP server does not expose repository tree and file content tools."
                )

            repo_paths = []
            last_error = None
            for args in self._tool_args_variants(tree_tool, repo_ref, branch):
                try:
                    result = await session.call_tool(tree_tool.name, args)
                    repo_paths = [
                        path for path in self._extract_paths(result)
                        if self._is_supported_file(path)
                    ]
                    if repo_paths:
                        break
                except Exception as exc:
                    last_error = exc

            if not repo_paths:
                if last_error:
                    raise RuntimeError(f"Failed to fetch Bitbucket repository tree via MCP: {last_error}")
                return "No relevant Bitbucket repository files were found."

            ranked_paths = sorted(
                ((path, self._score_file(path, terms)) for path in repo_paths),
                key=lambda item: (-item[1], len(item[0])),
            )
            ranked_paths = [item for item in ranked_paths if item[1] > 0][:max_files]
            if not ranked_paths:
                return "No relevant Bitbucket repository files were found."

            blocks = []
            for file_path, score in ranked_paths:
                file_text = ""
                for args in self._tool_args_variants(file_tool, repo_ref, branch, file_path=file_path):
                    try:
                        result = await session.call_tool(file_tool.name, args)
                        file_text = self._extract_text(result)
                        if file_text.strip():
                            break
                    except Exception:
                        continue

                if not file_text.strip():
                    continue

                snippet = self._extract_snippet(file_text, terms)
                if not snippet:
                    continue
                blocks.append(
                    f"File: {file_path}\n"
                    f"Source: Bitbucket MCP ({repo_ref['workspace']}/{repo_ref['repo_slug']})\n"
                    f"Branch: {branch or 'default'}\n"
                    f"Relevance score: {score}\n"
                    f"---\n"
                    f"{snippet}"
                )

            return "\n\n".join(blocks) if blocks else "No relevant Bitbucket repository files were found."
