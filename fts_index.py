import re
import sqlite3
from difflib import SequenceMatcher
from typing import Iterable, List, Dict, Any


class TestCaseFTSIndex:
    UPDATE_CONFIDENCE_THRESHOLD = 8.5
    STRONG_NAME_OR_STEPS_THRESHOLD = 0.55
    STOP_WORDS = {
        "и", "или", "в", "во", "на", "по", "для", "из", "с", "со", "к", "ко", "у",
        "о", "об", "от", "до", "над", "под", "при", "не", "но", "а", "то", "это",
        "как", "что", "если", "ли", "бы", "быть", "нужно", "надо", "при", "после",
        "перед", "через", "new", "feature", "doc", "docs",
    }

    def __init__(self, db_path: str = "fts_testcases.db") -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            existing_columns = []
            try:
                rows = conn.execute("PRAGMA table_info(testcases_fts)").fetchall()
                existing_columns = [row[1] for row in rows]
            except sqlite3.OperationalError:
                existing_columns = []

            expected_columns = ["id", "name", "fields", "precondition", "expected_result", "steps"]
            if existing_columns and existing_columns != expected_columns:
                conn.execute("DROP TABLE IF EXISTS testcases_fts")

            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS testcases_fts
                USING fts5(
                    id UNINDEXED,
                    name,
                    fields,
                    precondition,
                    expected_result,
                    steps
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

            steps = case.get("steps", "")
            if isinstance(steps, list):
                steps_text = "; ".join(str(step) for step in steps if step)
            else:
                steps_text = str(steps)

            rows.append(
                (
                    str(case_id),
                    str(case.get("name", "")),
                    fields_text,
                    str(case.get("precondition", "")),
                    str(case.get("expectedResult", case.get("expected_result", ""))),
                    steps_text,
                )
            )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM testcases_fts")
            conn.executemany(
                "INSERT INTO testcases_fts (id, name, fields, precondition, expected_result, steps) VALUES (?, ?, ?, ?, ?, ?)",
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

    @classmethod
    def _meaningful_tokens(cls, query: str) -> List[str]:
        tokens = cls._tokenize(query)
        result: List[str] = []
        for token in tokens:
            if len(token) <= 1:
                continue
            if token in cls.STOP_WORDS:
                continue
            if token not in result:
                result.append(token)
        return result

    @staticmethod
    def _token_coverage(tokens: List[str], text: str) -> float:
        if not tokens:
            return 0.0
        lowered = text.lower()
        matched = sum(1 for token in tokens if token in lowered)
        return matched / len(tokens)

    def _score_candidate(
        self,
        query: str,
        tokens: List[str],
        row: sqlite3.Row,
    ) -> Dict[str, float]:
        name = str(row["name"] or "")
        fields = str(row["fields"] or "")
        precondition = str(row["precondition"] or "")
        expected_result = str(row["expected_result"] or "")
        steps = str(row["steps"] or "")
        combined = " ".join([name, fields, precondition, expected_result, steps]).lower()
        normalized_query = query.lower()

        name_coverage = self._token_coverage(tokens, name)
        fields_coverage = self._token_coverage(tokens, fields)
        steps_coverage = self._token_coverage(tokens, steps)
        full_coverage = self._token_coverage(tokens, combined)
        phrase_bonus = 1.0 if normalized_query and normalized_query in combined else 0.0
        sequence_ratio = SequenceMatcher(None, normalized_query, f"{name.lower()} {steps.lower()}").ratio()

        bm25_rank = row["rank"]
        try:
            bm25_rank = float(bm25_rank)
        except (TypeError, ValueError):
            bm25_rank = 0.0

        fts_bonus = 1 / (1 + max(bm25_rank, 0.0))

        total_score = (
            full_coverage * 6.0
            + name_coverage * 4.0
            + steps_coverage * 3.0
            + fields_coverage * 2.0
            + phrase_bonus * 3.5
            + sequence_ratio * 2.5
            + fts_bonus * 1.5
        )
        return {
            "score": total_score,
            "name_coverage": name_coverage,
            "fields_coverage": fields_coverage,
            "steps_coverage": steps_coverage,
            "full_coverage": full_coverage,
            "phrase_bonus": phrase_bonus,
            "sequence_ratio": sequence_ratio,
            "fts_bonus": fts_bonus,
        }

    def _build_search_queries(self, normalized: str, tokens: List[str]) -> List[str]:
        queries: List[str] = []
        if normalized:
            phrase = normalized.replace('"', ' ').strip()
            if phrase:
                queries.append(f'"{phrase}"')

        if tokens:
            top_tokens = tokens[:6]
            queries.append(" AND ".join(top_tokens))
            queries.append(" AND ".join(f"{token}*" for token in top_tokens))
            queries.append(" OR ".join(tokens))

        return list(dict.fromkeys(q for q in queries if q.strip()))

    def search_detailed(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        normalized = self._normalize_query(query)
        tokens = self._meaningful_tokens(normalized)
        if not tokens:
            tokens = self._tokenize(normalized)
        if not tokens:
            return []

        candidates: Dict[str, Dict[str, Any]] = {}
        search_queries = self._build_search_queries(normalized, tokens)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for fts_query in search_queries:
                rows = conn.execute(
                    """
                    SELECT
                        id,
                        name,
                        fields,
                        precondition,
                        expected_result,
                        steps,
                        bm25(testcases_fts, 8.0, 4.0, 2.0, 1.5, 3.5) AS rank
                    FROM testcases_fts
                    WHERE testcases_fts MATCH ?
                    LIMIT ?
                    """,
                    (fts_query, max(limit * 4, 20)),
                ).fetchall()

                for row in rows:
                    case_id = str(row["id"])
                    score_details = self._score_candidate(normalized, tokens, row)
                    existing = candidates.get(case_id)
                    if existing is None or score_details["score"] > existing["score"]:
                        candidates[case_id] = {
                            "id": case_id,
                            "name": str(row["name"] or ""),
                            "score": score_details["score"],
                            "name_coverage": score_details["name_coverage"],
                            "fields_coverage": score_details["fields_coverage"],
                            "steps_coverage": score_details["steps_coverage"],
                            "full_coverage": score_details["full_coverage"],
                            "phrase_bonus": score_details["phrase_bonus"],
                            "sequence_ratio": score_details["sequence_ratio"],
                            "fts_bonus": score_details["fts_bonus"],
                            "matched_by": fts_query,
                        }

        ranked_candidates = sorted(
            candidates.values(),
            key=lambda item: item["score"],
            reverse=True,
        )
        for item in ranked_candidates:
            item["update_candidate"] = bool(
                item["score"] >= self.UPDATE_CONFIDENCE_THRESHOLD
                or item["name_coverage"] >= self.STRONG_NAME_OR_STEPS_THRESHOLD
                or item["steps_coverage"] >= self.STRONG_NAME_OR_STEPS_THRESHOLD
            )
        return ranked_candidates[:limit]

    def search(self, query: str, limit: int = 10) -> List[str]:
        return [item["id"] for item in self.search_detailed(query, limit=limit)]
