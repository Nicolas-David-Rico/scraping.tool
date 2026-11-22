from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any, Optional
from bs4 import BeautifulSoup

from app.models import ProductOffer, StoreInfo
from app.scrapers.base import BaseStoreScraper, calc_discount, make_offer_id, parse_cop_price
from app.scrapers.browser_pool import browser_pool


class AlkostoScraper(BaseStoreScraper):
    store = StoreInfo(
        id="alkosto",
        name="Alkosto",
        domain="www.alkosto.com",
        color="#ea580c",
        badge_bg="rgba(234, 88, 12, 0.16)",
        card_name="Tarjeta Alkosto / Medios de pago",
    )
    base_url = "https://www.alkosto.com"

    # Cache in-memory Algolia public search config extracted from alkosto.com
    _algolia_cache: Optional[tuple[str, str, str, float]] = None
    _cache_ttl: float = 1800.0

    async def search(
        self,
        query: str,
        max_results: int = 10,
        use_playwright: bool = True,
        timeout: float = 25.0,
    ) -> tuple[list[ProductOffer], str]:
        q_enc = urllib.parse.quote(query)
        search_url = f"{self.base_url}/search/?text={q_enc}"

        async with self.create_http_client(
            timeout=min(timeout, 18.0),
            extra_headers={"Referer": f"{self.base_url}/"},
        ) as client:
            # 1) Try cached Algolia config first (ultra-fast ~200ms)
            cfg = self._get_cached_algolia()
            if cfg:
                offers = await self._query_algolia(client, cfg[0], cfg[1], cfg[2], query, max_results)
                if offers:
                    return offers[:max_results], "algolia-api"

            # 2) Fetch search page HTML to extract fresh Algolia public search key
            try:
                resp = await client.get(search_url)
                if resp.status_code == 200:
                    html = resp.content.decode("utf-8", errors="replace")
                    fresh_cfg = self._extract_algolia_config(html)
                    if fresh_cfg:
                        self._algolia_cache = (fresh_cfg[0], fresh_cfg[1], fresh_cfg[2], time.time())
                        offers = await self._query_algolia(
                            client, fresh_cfg[0], fresh_cfg[1], fresh_cfg[2], query, max_results
                        )
                        if offers:
                            return offers[:max_results], "algolia-api"

                    # Fallback SSR HTML parse if present
                    offers = self._parse_html(html)
                    if offers:
                        return offers[:max_results], "html"
            except Exception:
                pass

        # 3) Playwright fallback
        if use_playwright:
            offers = await self._search_via_playwright(search_url, max_results, timeout)
            if offers:
                return offers[:max_results], "playwright"

        return [], "alkosto"

    def _get_cached_algolia(self) -> Optional[tuple[str, str, str]]:
        if not self._algolia_cache:
            return None
        app_id, api_key, index_name, ts = self._algolia_cache
        if time.time() - ts < self._cache_ttl:
            return app_id, api_key, index_name
        return None

    @staticmethod
    def _extract_algolia_config(html: str) -> Optional[tuple[str, str, str]]:
        # Match the `algolia : { ... }` config block in Alkosto HTML
        block_m = re.search(r"algolia\s*:\s*\{(.{0,1200}?)\}", html, re.S)
        scope = block_m.group(1) if block_m else html
        app_m = re.search(r"applicationId\s*:\s*['\"]([A-Z0-9]{8,16})['\"]", scope)
        key_m = re.search(r"apiKey\s*:\s*['\"]([A-Za-z0-9+/=_\-]{20,300})['\"]", scope)
        idx_m = re.search(r"indexName\s*:\s*['\"]([A-Za-z0-9_\-\.]+)['\"]", scope)
        if app_m and key_m:
            return app_m.group(1), key_m.group(1), idx_m.group(1) if idx_m else "alkostoIndexAlgoliaPRD"
        return None

    async def _query_algolia(
        self,
        client,
        app_id: str,
        api_key: str,
        index_name: str,
        query: str,
        max_results: int,
    ) -> list[ProductOffer]:
        url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{index_name}/query"
        headers = {
            "X-Algolia-Application-Id": app_id,
            "X-Algolia-API-Key": api_key,
            "Content-Type": "application/json",
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
        }
        try:
            r = await client.post(
                url,
                headers=headers,
                json={"query": query, "hitsPerPage": max(max_results, 12)},
            )
            if r.status_code == 200:
                hits = (r.json() or {}).get("hits") or []
                return self._parse_algolia_hits(hits)
        except Exception:
            pass
        return []

    def _parse_algolia_hits(self, hits: list[dict[str, Any]]) -> list[ProductOffer]:
        offers: list[ProductOffer] = []
        for h in hits:
            try:
                title = (h.get("name_text_es") or h.get("name") or h.get("title") or "").strip()
                if not title:
                    continue

                price = parse_cop_price(
                    h.get("lowestprice_double")
                    or h.get("discountprice_double")
                    or h.get("pricevalue_cop_double")
                    or h.get("price")
                )
                if not price:
                    continue

                list_price = parse_cop_price(
                    h.get("pricevalue_cop_double")
                    or h.get("baseprice_cop_string")
                    or h.get("listprice_cop_double")
                    or h.get("oldprice_cop_double")
                )
                if list_price and list_price <= price:
                    list_price = None

                discount = calc_discount(price, list_price)
                if not discount and h.get("percentagediscount_string"):
                    m = re.search(r"(\d+)", str(h.get("percentagediscount_string")))
                    if m:
                        discount = int(m.group(1))

                raw_url = h.get("url_es_string") or h.get("url") or ""
                if not raw_url:
                    continue
                url = (
                    raw_url
                    if raw_url.startswith("http")
                    else f"{self.base_url}{raw_url if raw_url.startswith('/') else '/' + raw_url}"
                )

                img = (
                    h.get("img-310wx310h_string")
                    or h.get("img-515wx515h_string")
                    or h.get("img-1400wx1400h_string")
                    or h.get("image")
                    or ""
                )
                if img and img.startswith("/"):
                    img = f"{self.base_url}{img}"

                brand_raw = h.get("marca_text") or h.get("brand_string_mv") or h.get("marca_string_mv") or ""
                if isinstance(brand_raw, list):
                    brand = str(brand_raw[0]) if brand_raw else ""
                else:
                    brand = str(brand_raw)

                badges: list[str] = []
                if h.get("enviogratis_boolean") or h.get("freeShipping"):
                    badges.append("Envío gratis")
                if h.get("pickupavailableflag_boolean"):
                    badges.append("Recoge en tienda")

                pay_promos = h.get("paymentpromotion_text_mv") or []
                for p in pay_promos:
                    s = str(p).strip()
                    if s and len(s) <= 45 and s not in badges:
                        badges.append(s)

                in_stock = bool(h.get("instockflag_boolean", True))
                ship_info = "Envío gratis" if "Envío gratis" in badges else ("Recoge en tienda" if h.get("pickupavailableflag_boolean") else None)

                offers.append(
                    ProductOffer(
                        id=make_offer_id(self.store.id, url, title),
                        store_id=self.store.id,
                        store_name=self.store.name,
                        store_color=self.store.color,
                        title=title,
                        brand=brand.strip(),
                        price=price,
                        list_price=list_price,
                        discount_percent=discount,
                        promo_badges=badges[:4],
                        shipping_info=ship_info,
                        url=url,
                        image_url=img or None,
                        sku=str(h.get("code_string") or h.get("objectID") or "") or None,
                        seller="Alkosto",
                        in_stock=in_stock,
                    )
                )
            except Exception:
                continue
        return offers

    def _parse_html(self, html: str) -> list[ProductOffer]:
        soup = BeautifulSoup(html, "lxml")
        offers: list[ProductOffer] = []
        cards = soup.select("li.ais-InfiniteHits-item, li.product__item, div.product__item")
        for card in cards:
            a = card.select_one("a.product__item__top__link, a[href*='/p/'], a[href]")
            title_el = card.select_one("h3, .product__item__top__title")
            price_el = card.select_one(".product__price--discounts__price, .price, [class*='price']")
            old_el = card.select_one(".product__price--discounts__old, [class*='old']")
            img_el = card.select_one("img")

            title = (title_el.get_text(" ", strip=True) if title_el else (a.get("title") if a else "") or "").strip()
            href = a.get("href", "") if a else ""
            price = parse_cop_price(price_el.get_text(" ", strip=True) if price_el else "")
            if not title or not href or not price:
                continue
            url = href if href.startswith("http") else f"{self.base_url}{href if href.startswith('/') else '/' + href}"
            list_p = parse_cop_price(old_el.get_text(" ", strip=True) if old_el else "")
            if list_p and list_p <= price:
                list_p = None
            img = ""
            if img_el:
                img = img_el.get("src") or img_el.get("data-src") or ""
                if img.startswith("/"):
                    img = f"{self.base_url}{img}"

            offers.append(
                ProductOffer(
                    id=make_offer_id(self.store.id, url, title),
                    store_id=self.store.id,
                    store_name=self.store.name,
                    store_color=self.store.color,
                    title=title,
                    price=price,
                    list_price=list_p,
                    discount_percent=calc_discount(price, list_p),
                    url=url,
                    image_url=img or None,
                    seller="Alkosto",
                )
            )
        return offers

    async def _search_via_playwright(self, search_url: str, max_results: int, timeout: float) -> list[ProductOffer]:
        captured_hits: list[dict[str, Any]] = []
        async with browser_pool.new_page() as page:
            if page is None:
                return []

            async def on_response(response):
                try:
                    if "algolia" in response.url.lower() and response.status == 200:
                        data = await response.json()
                        if isinstance(data, dict):
                            if isinstance(data.get("hits"), list):
                                captured_hits.extend(data["hits"])
                            for res in data.get("results") or []:
                                if isinstance(res, dict) and isinstance(res.get("hits"), list):
                                    captured_hits.extend(res["hits"])
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                await page.wait_for_timeout(3500)
            except Exception:
                pass

            if captured_hits:
                parsed = self._parse_algolia_hits(captured_hits)
                if parsed:
                    return parsed[:max_results]

            html = await page.content()
            return self._parse_html(html)[:max_results]
