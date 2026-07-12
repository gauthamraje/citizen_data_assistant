"""Load and search the Samaajadata golden-standards mission library."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rank_bm25 import BM25Okapi

POLISHED_COLUMNS = {
    "Action_Title",
    "Story_Context",
    "Primary_Goal",
    "Action_Steps",
    "Communication_Script",
}

LEGACY_COLUMNS = {"Title", "User_Story_x", "AI_Fix_Summary", "AI_Fix_FullText"}

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class Mission:
    id: int
    title: str
    text: str
    row: Dict[str, str]


class KnowledgeBase:
    def __init__(self, csv_path: Path, top_k: int = 6):
        self.csv_path = csv_path
        self.top_k = top_k
        self.missions: List[Mission] = []
        self._bm25: Optional[BM25Okapi] = None
        self._corpus_tokens: List[List[str]] = []

    @property
    def loaded(self) -> bool:
        return bool(self.missions)

    @property
    def mission_count(self) -> int:
        return len(self.missions)

    def load(self) -> None:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Knowledge base not found: {self.csv_path}")

        with self.csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        if not rows:
            raise ValueError(f"No missions found in {self.csv_path}")

        schema = set(rows[0].keys())
        use_polished = bool(schema & POLISHED_COLUMNS)

        self.missions = []
        for index, row in enumerate(rows):
            cleaned = {key: (value or "").strip() for key, value in row.items()}
            title = (
                cleaned.get("Action_Title")
                or cleaned.get("Title")
                or f"Mission {index + 1}"
            )
            text = (
                self._format_polished_mission(cleaned)
                if use_polished
                else self._format_legacy_mission(cleaned)
            )
            self.missions.append(
                Mission(id=index, title=title, text=text, row=cleaned)
            )

        self._corpus_tokens = [_tokenize(m.text) for m in self.missions]
        self._bm25 = BM25Okapi(self._corpus_tokens)

    def search(self, query: str, top_k: Optional[int] = None) -> List[Mission]:
        if not self._bm25:
            raise RuntimeError("Knowledge base not loaded.")

        limit = top_k or self.top_k
        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        ranked = sorted(
            range(len(scores)),
            key=lambda index: scores[index],
            reverse=True,
        )

        results: List[Mission] = []
        for index in ranked[:limit]:
            if scores[index] <= 0:
                continue
            results.append(self.missions[index])
        return results

    def build_search_result_blocks(self, query: str) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for mission in self.search(query):
            blocks.append(
                {
                    "type": "search_result",
                    "source": f"kb://mission/{mission.id}",
                    "title": mission.title,
                    "content": [{"type": "text", "text": mission.text}],
                    "citations": {"enabled": False},
                }
            )
        return blocks

    @staticmethod
    def _format_polished_mission(row: Dict[str, str]) -> str:
        fields = [
            ("Action_Title", "Action Title"),
            ("Complexity_and_Time", "Complexity and Time"),
            ("Story_Context", "Story Context"),
            ("Primary_Goal", "Primary Goal"),
            ("Action_Steps", "Action Steps"),
            ("Communication_Script", "Communication Script"),
            ("Problem_Solving_Tips", "Problem Solving Tips"),
            ("Success_Evidence", "Success Evidence"),
            ("Identified_Gaps_and_Fixes", "Identified Gaps and Fixes"),
            ("Technical_Resources", "Technical Resources"),
            ("Source_Reference", "Source Reference"),
        ]
        parts = []
        for key, label in fields:
            value = row.get(key, "")
            if value:
                parts.append(f"{label}:\n{value}")
        return "\n\n".join(parts)

    @staticmethod
    def _format_legacy_mission(row: Dict[str, str]) -> str:
        fields = [
            ("Title", "Title"),
            ("User_Story_x", "Story"),
            ("Gap_Analysis_x", "Gap Analysis"),
            ("User_Story_y", "Additional Story"),
            ("Gap_Analysis_y", "Additional Gap Analysis"),
            ("AI_Fix_Summary", "Fix Summary"),
            ("AI_Fix_FullText", "Fix Details"),
            ("AI_Fix_Source", "Source"),
            ("AI_Confidence_Score", "Confidence Score"),
            ("AI_Mentor_Needed", "Mentor Needed"),
        ]
        parts = []
        for key, label in fields:
            value = row.get(key, "")
            if value:
                parts.append(f"{label}:\n{value}")
        return "\n\n".join(parts)


def build_messages_for_claude(
  history: Sequence[Dict[str, Any]],
  user_text: str,
  search_blocks: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Replay thread history; attach retrieval only to the latest user turn."""
    messages: List[Dict[str, Any]] = []
    for index, item in enumerate(history):
        is_last = index == len(history) - 1
        if item["role"] == "user" and is_last:
            content: Any = [{"type": "text", "text": user_text}, *search_blocks]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": item["role"], "content": item["content"]})
    return messages
