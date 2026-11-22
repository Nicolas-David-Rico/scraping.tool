from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class StoreInfo(BaseModel):
    id: str
    name: str
    domain: str
    color: str
    badge_bg: str
    card_name: Optional[str] = None
    enabled_by_default: bool = True


class ProductOffer(BaseModel):
    id: str
    store_id: str
    store_name: str
    store_color: str
    title: str
    brand: str = ""
    price: float
    list_price: Optional[float] = None
    discount_percent: Optional[int] = None
    card_price: Optional[float] = None
    card_promo_text: Optional[str] = None
    promo_badges: list[str] = Field(default_factory=list)
    currency: str = "COP"
    url: str
    image_url: Optional[str] = None
    sku: Optional[str] = None
    seller: Optional[str] = None
    in_stock: bool = True
    shipping_info: Optional[str] = None
    relevance_score: float = 0.0
    scraped_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class StoreSearchStatus(BaseModel):
    store_id: str
    store_name: str
    store_color: str = "#64748b"
    status: Literal["ok", "empty", "error", "blocked", "timeout"] = "ok"
    count: int = 0
    elapsed_ms: int = 0
    method: str = "api"
    error_message: Optional[str] = None


class QueryIntent(BaseModel):
    raw_message: str
    search_query: str
    must_include: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    category_hint: Optional[str] = None
    intent_summary: str = ""
    used_ai: bool = False


class PriceStats(BaseModel):
    total_offers: int = 0
    stores_with_results: int = 0
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    avg_price: Optional[float] = None
    median_price: Optional[float] = None
    savings_vs_max: Optional[float] = None
    savings_vs_avg: Optional[float] = None
    max_discount_percent: Optional[int] = None


class SearchComparison(BaseModel):
    search_id: str
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    raw_message: str
    intent: QueryIntent
    best_offer: Optional[ProductOffer] = None
    best_card_offer: Optional[ProductOffer] = None
    best_discount_offer: Optional[ProductOffer] = None
    stats: PriceStats = Field(default_factory=PriceStats)
    offers: list[ProductOffer] = Field(default_factory=list)
    store_statuses: list[StoreSearchStatus] = Field(default_factory=list)
    ai_analysis: str = ""
    ai_used: bool = False
    ai_provider: Optional[str] = None
    ai_error: Optional[str] = None


class ChatSearchRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=600)
    stores: Optional[list[str]] = None
    max_results_per_store: Optional[int] = None


class FollowUpRequest(BaseModel):
    search_id: str
    question: str = Field(..., min_length=1, max_length=600)
    history: list[dict[str, str]] = Field(default_factory=list)


class AppSettingsPayload(BaseModel):
    ai_provider: Literal["gemini", "openai"] = "gemini"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    firecrawl_api_key: Optional[str] = None
    firecrawl_mode: Literal["fallback", "primary", "off"] = "fallback"
    use_playwright_fallback: bool = True
    store_timeout_seconds: int = 25
    max_results_per_store: int = 10
