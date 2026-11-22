from __future__ import annotations

import urllib.parse
from typing import Any, Optional
import httpx

from app.config import settings_manager
from app.models import ProductOffer, StoreInfo
from app.scrapers.base import calc_discount, make_offer_id, parse_cop_price

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v1"

OFFERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "brand": {"type": "string"},
                    "price_cop": {"type": "number"},
                    "list_price_cop": {"type": "number"},
                    "discount_percent": {"type": "number"},
                    "card_price_cop": {"type": "number"},
                    "card_promo_text": {"type": "string"},
                    "url": {"type": "string"},
                    "image_url": {"type": "string"},
                    "shipping_info": {"type": "string"},
                    "seller": {"type": "string"},
                },
                "required": ["title", "price_cop"],
            },
        }
    },
    "required": ["products"],
}

STORE_SEARCH_URLS: dict[str, str] = {
    "exito": "https://www.exito.com/s?q={q}",
    "alkosto": "https://www.alkosto.com/search/?text={q}",
    "falabella": "https://www.falabella.com.co/falabella-co/search?Ntt={q}",
    "jumbo": "https://www.jumbocolombia.com/{q}?_q={q}&map=ft",
    "olimpica": "https://www.olimpica.com/{q}?_q={q}&map=ft",
    "homecenter": "https://www.homecenter.com.co/homecenter-co/search/?Ntt={q}",
    "mercadolibre": "https://listado.mercadolibre.com.co/{q_slug}",
}


class FirecrawlService:
    """Integración directa con Firecrawl REST API (/v1/scrape y /v1/search) para OfertaCO."""

    def get_config(self) -> tuple[str, str]:
        cfg = settings_manager.get_all()
        api_key = (cfg.get("firecrawl_api_key") or "").strip()
        mode = (cfg.get("firecrawl_mode") or "fallback").strip().lower()
        return api_key, mode

    def is_enabled_for_fallback(self) -> bool:
        api_key, mode = self.get_config()
        return bool(api_key) and mode in ("fallback", "primary")

    def is_primary_mode(self) -> bool:
        api_key, mode = self.get_config()
        return bool(api_key) and mode == "primary"

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def test_connection(self, api_key: Optional[str] = None) -> dict[str, Any]:
        if not api_key or api_key.startswith("•••"):
            api_key, _ = self.get_config()
        if not api_key:
            return {"ok": False, "message": "No hay Firecrawl API Key configurada."}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{FIRECRAWL_BASE_URL}/search",
                    headers=self._headers(api_key),
                    json={"query": "exito colombia", "limit": 1, "lang": "es", "country": "CO"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success", True):
                        return {"ok": True, "message": "Conexión exitosa con Firecrawl API."}
                if resp.status_code in (401, 403):
                    return {"ok": False, "message": "API Key de Firecrawl inválida o sin permisos (HTTP 401/403)."}
                if resp.status_code == 402:
                    return {"ok": False, "message": "Créditos de Firecrawl agotados (HTTP 402)."}
                return {"ok": False, "message": f"Firecrawl respondió HTTP {resp.status_code}: {resp.text[:180]}"}
        except Exception as exc:
            return {"ok": False, "message": f"Error conectando con Firecrawl: {exc}"}

    async def scrape_store_offers(
        self,
        store: StoreInfo,
        query: str,
        max_results: int = 10,
        timeout: float = 35.0,
    ) -> list[ProductOffer]:
        """Extrae ofertas estructuradas de una tienda colombiana usando Firecrawl /v1/scrape + /v1/search."""
        api_key, _ = self.get_config()
        if not api_key:
            return []

        q_enc = urllib.parse.quote(query)
        q_slug = urllib.parse.quote(query.strip().replace(" ", "-"))
        template = STORE_SEARCH_URLS.get(store.id)
        if not template:
            return []

        target_url = template.format(q=q_enc, q_slug=q_slug)
        base_domain = f"https://{store.domain}"

        # 1) Try structured JSON extraction via /v1/scrape on the store search page
        offers = await self._scrape_url_json(
            api_key=api_key,
            store=store,
            target_url=target_url,
            base_domain=base_domain,
            query=query,
            max_results=max_results,
            timeout=timeout,
        )
        if offers:
            return offers[:max_results]

        # 2) Fallback: Firecrawl /v1/search scoped to `site:<domain>` + scrape top results
        offers = await self._search_domain_offers(
            api_key=api_key,
            store=store,
            base_domain=base_domain,
            query=query,
            max_results=max_results,
            timeout=timeout,
        )
        return offers[:max_results]

    async def _scrape_url_json(
        self,
        *,
        api_key: str,
        store: StoreInfo,
        target_url: str,
        base_domain: str,
        query: str,
        max_results: int,
        timeout: float,
    ) -> list[ProductOffer]:
        prompt = (
            f"Extrae hasta {max_results} productos relevantes para la búsqueda '{query}' en esta página de {store.name} Colombia. "
            "Los precios deben ser números enteros en pesos colombianos (COP, sin puntos ni símbolos). "
            "Incluye el precio de oferta (price_cop), precio antes si existe (list_price_cop), "
            "porcentaje de descuento (discount_percent), precio especial con tarjeta de la tienda si aparece "
            "(card_price_cop y card_promo_text), URL del producto (url) e imagen (image_url)."
        )
        payload = {
            "url": target_url,
            "formats": ["json"],
            "onlyMainContent": True,
            "waitFor": 2500,
            "timeout": int(min(timeout, 45) * 1000),
            "jsonOptions": {
                "prompt": prompt,
                "schema": OFFERS_JSON_SCHEMA,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=timeout + 5.0) as client:
                resp = await client.post(
                    f"{FIRECRAWL_BASE_URL}/scrape",
                    headers=self._headers(api_key),
                    json=payload,
                )
                if resp.status_code != 200:
                    return []
                body = resp.json() or {}
                data = body.get("data") or {}
                extracted = data.get("json") or data.get("extract") or {}
                raw_products = extracted.get("products") if isinstance(extracted, dict) else []
                return self._normalize_extracted(raw_products, store, base_domain, target_url)
        except Exception:
            return []

    async def _search_domain_offers(
        self,
        *,
        api_key: str,
        store: StoreInfo,
        base_domain: str,
        query: str,
        max_results: int,
        timeout: float,
    ) -> list[ProductOffer]:
        payload = {
            "query": f"site:{store.domain} {query} precio",
            "limit": min(max_results, 6),
            "lang": "es",
            "country": "CO",
            "scrapeOptions": {
                "formats": ["json"],
                "onlyMainContent": True,
                "jsonOptions": {
                    "prompt": (
                        f"Extrae los datos del producto y precio en pesos colombianos (COP) de {store.name} Colombia."
                    ),
                    "schema": OFFERS_JSON_SCHEMA,
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=timeout + 5.0) as client:
                resp = await client.post(
                    f"{FIRECRAWL_BASE_URL}/search",
                    headers=self._headers(api_key),
                    json=payload,
                )
                if resp.status_code != 200:
                    return []
                body = resp.json() or {}
                items = body.get("data") or []
                collected: list[ProductOffer] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_url = item.get("url") or base_domain
                    extracted = item.get("json") or item.get("extract") or {}
                    raw_prods = extracted.get("products") if isinstance(extracted, dict) else []
                    collected.extend(self._normalize_extracted(raw_prods, store, base_domain, item_url))
                return collected
        except Exception:
            return []

    def _normalize_extracted(
        self,
        raw_products: Any,
        store: StoreInfo,
        base_domain: str,
        fallback_url: str,
    ) -> list[ProductOffer]:
        if not isinstance(raw_products, list):
            return []
        offers: list[ProductOffer] = []
        for p in raw_products:
            if not isinstance(p, dict):
                continue
            title = str(p.get("title") or "").strip()
            price = parse_cop_price(p.get("price_cop"))
            if not title or not price:
                continue

            list_price = parse_cop_price(p.get("list_price_cop"))
            if list_price and list_price <= price:
                list_price = None

            disc = calc_discount(price, list_price)
            if not disc and p.get("discount_percent"):
                try:
                    d = int(round(float(p["discount_percent"])))
                    if 1 <= d <= 95:
                        disc = d
                except Exception:
                    pass

            card_price = parse_cop_price(p.get("card_price_cop"))
            if card_price and card_price >= price:
                card_price = None
            card_text = str(p.get("card_promo_text") or "").strip() or (store.card_name if card_price else None)

            raw_url = str(p.get("url") or "").strip()
            if not raw_url:
                url = fallback_url
            elif raw_url.startswith("http"):
                url = raw_url
            elif raw_url.startswith("/"):
                url = f"{base_domain}{raw_url}"
            else:
                url = f"{base_domain}/{raw_url}"

            img = str(p.get("image_url") or "").strip() or None
            if img and img.startswith("/"):
                img = f"{base_domain}{img}"

            ship = str(p.get("shipping_info") or "").strip() or None
            badges = ["Firecrawl"]
            if ship:
                badges.append(ship)

            offers.append(
                ProductOffer(
                    id=make_offer_id(store.id, url, title),
                    store_id=store.id,
                    store_name=store.name,
                    store_color=store.color,
                    title=title,
                    brand=str(p.get("brand") or "").strip(),
                    price=price,
                    list_price=list_price,
                    discount_percent=disc,
                    card_price=card_price,
                    card_promo_text=card_text,
                    promo_badges=badges[:3],
                    shipping_info=ship,
                    url=url,
                    image_url=img,
                    seller=str(p.get("seller") or store.name).strip(),
                )
            )
        return offers


firecrawl_service = FirecrawlService()
