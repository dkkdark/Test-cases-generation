import re
import sqlite3
from typing import Iterable, List, Dict, Any


class TestCaseFTSIndex:
    def __init__(self, db_path: str = "fts_testcases.db") -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS testcases_fts
                USING fts5(
                    id UNINDEXED,
                    name,
                    fields,
                    precondition,
                    expected_result
                );
                """
            )

    def rebuild(self, cases: Iterable[Dict[str, Any]]) -> int:
        rows = []
        for case in cases:
            if not isinstance(case, dict):
                continue
            case_id = case.get("id")
            if case_id is None:
                continue
            fields = case.get("fields", "")
            if isinstance(fields, list):
                field_pairs = []
                for item in fields:
                    if isinstance(item, dict):
                        name = item.get("fieldName")
                        value = item.get("fieldValue")
                        if name and value:
                            field_pairs.append(f"{name}: {value}")
                fields_text = "; ".join(field_pairs)
            else:
                fields_text = str(fields)

            rows.append(
                (
                    str(case_id),
                    str(case.get("name", "")),
                    fields_text,
                    str(case.get("precondition", "")),
                    str(case.get("expectedResult", case.get("expected_result", ""))),
                )
            )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM testcases_fts")
            conn.executemany(
                "INSERT INTO testcases_fts (id, name, fields, precondition, expected_result) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)

    @staticmethod
    def _normalize_query(query: str, max_len: int = 200) -> str:
        q = " ".join(query.split())
        return q[:max_len]

    @staticmethod
    def _tokenize(query: str) -> List[str]:
        return re.findall(r"\w+", query.lower())

    def search(self, query: str, limit: int = 10) -> List[str]:
        normalized = self._normalize_query(query)
        tokens = self._tokenize(normalized)
        if not tokens:
            return []
        fts_query = " OR ".join(tokens)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM testcases_fts WHERE testcases_fts MATCH ? LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        return [row[0] for row in rows]
