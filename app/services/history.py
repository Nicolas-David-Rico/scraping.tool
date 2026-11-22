from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from app.config import DATA_DIR
from app.models import SearchComparison

HISTORY_FILE = DATA_DIR / "history.json"
MAX_HISTORY_ITEMS = 40


class HistoryManager:
    """Almacena localmente las búsquedas y comparativas en data/history.json."""

    def __init__(self, path: Path = HISTORY_FILE) -> None:
        self.path = path
        self._items: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._items = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self._items = raw
        except Exception:
            self._items = []

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._items[:MAX_HISTORY_ITEMS], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def add(self, comparison: SearchComparison) -> None:
        data = comparison.model_dump()
        self._items = [item for item in self._items if item.get("search_id") != comparison.search_id]
        self._items.insert(0, data)
        self._items = self._items[:MAX_HISTORY_ITEMS]
        self._save()

    def list_summaries(self) -> list[dict]:
        out = []
        for item in self._items:
            best = item.get("best_offer") or {}
            stats = item.get("stats") or {}
            out.append(
                {
                    "search_id": item.get("search_id"),
                    "created_at": item.get("created_at"),
                    "raw_message": item.get("raw_message"),
                    "search_query": (item.get("intent") or {}).get("search_query", item.get("raw_message")),
                    "total_offers": stats.get("total_offers", 0),
                    "stores_with_results": stats.get("stores_with_results", 0),
                    "min_price": stats.get("min_price"),
                    "best_store": best.get("store_name"),
                }
            )
        return out

    def get(self, search_id: str) -> Optional[SearchComparison]:
        for item in self._items:
            if item.get("search_id") == search_id:
                try:
                    return SearchComparison.model_validate(item)
                except Exception:
                    return None
        return None

    def delete(self, search_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.get("search_id") != search_id]
        if len(self._items) != before:
            self._save()
            return True
        return False


history_manager = HistoryManager()
