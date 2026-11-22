from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"

load_dotenv(BASE_DIR / ".env")


class SettingsManager:
    """Gestiona configuración local combinando .env y data/settings.json."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        defaults: dict[str, Any] = {
            "ai_provider": os.getenv("AI_PROVIDER", "gemini").strip().lower() or "gemini",
            "gemini_api_key": os.getenv("GEMINI_API_KEY", "").strip(),
            "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
            "openai_api_key": os.getenv("OPENAI_API_KEY", "").strip(),
            "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
            "firecrawl_api_key": os.getenv("FIRECRAWL_API_KEY", "").strip(),
            "firecrawl_mode": os.getenv("FIRECRAWL_MODE", "fallback").strip().lower() or "fallback",
            "use_playwright_fallback": os.getenv("USE_PLAYWRIGHT_FALLBACK", "true").lower() in ("1", "true", "yes"),
            "store_timeout_seconds": int(os.getenv("STORE_TIMEOUT_SECONDS", "25")),
            "max_results_per_store": int(os.getenv("MAX_RESULTS_PER_STORE", "10")),
        }
        if SETTINGS_FILE.exists():
            try:
                saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        if v is not None:
                            defaults[k] = v
            except Exception:
                pass
        self._data = defaults

    def get_all(self) -> dict[str, Any]:
        return dict(self._data)

    def get_public(self) -> dict[str, Any]:
        d = self.get_all()
        gemini_key = d.get("gemini_api_key", "")
        openai_key = d.get("openai_api_key", "")
        firecrawl_key = d.get("firecrawl_api_key", "")
        return {
            "ai_provider": d["ai_provider"],
            "gemini_model": d["gemini_model"],
            "openai_model": d["openai_model"],
            "gemini_configured": bool(gemini_key),
            "openai_configured": bool(openai_key),
            "gemini_key_preview": self._mask_key(gemini_key),
            "openai_key_preview": self._mask_key(openai_key),
            "firecrawl_configured": bool(firecrawl_key),
            "firecrawl_key_preview": self._mask_key(firecrawl_key),
            "firecrawl_mode": d.get("firecrawl_mode", "fallback"),
            "use_playwright_fallback": bool(d["use_playwright_fallback"]),
            "store_timeout_seconds": int(d["store_timeout_seconds"]),
            "max_results_per_store": int(d["max_results_per_store"]),
        }

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        for key, value in patch.items():
            if value is None:
                continue
            # Do not overwrite stored key if frontend sent masked placeholder or empty marker unchanged
            if key in ("gemini_api_key", "openai_api_key", "firecrawl_api_key"):
                if isinstance(value, str) and value.startswith("•••"):
                    continue
                self._data[key] = value.strip()
            else:
                self._data[key] = value
        SETTINGS_FILE.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.get_public()

    def get_active_ai_config(self) -> tuple[str | None, str, str]:
        """Returns (provider, api_key, model) if an API key is available."""
        provider = self._data.get("ai_provider", "gemini")
        if provider == "openai" and self._data.get("openai_api_key"):
            return "openai", self._data["openai_api_key"], self._data.get("openai_model", "gpt-4o-mini")
        if provider == "gemini" and self._data.get("gemini_api_key"):
            return "gemini", self._data["gemini_api_key"], self._data.get("gemini_model", "gemini-2.5-flash")
        # Fallback to whichever key is configured
        if self._data.get("gemini_api_key"):
            return "gemini", self._data["gemini_api_key"], self._data.get("gemini_model", "gemini-2.5-flash")
        if self._data.get("openai_api_key"):
            return "openai", self._data["openai_api_key"], self._data.get("openai_model", "gpt-4o-mini")
        return None, "", ""

    @staticmethod
    def _mask_key(key: str) -> str:
        if not key:
            return ""
        if len(key) <= 8:
            return "•••"
        return f"•••{key[-4:]}"


settings_manager = SettingsManager()
