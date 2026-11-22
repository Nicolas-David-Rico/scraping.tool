from __future__ import annotations

import json
import urllib.parse
from typing import Any, Optional
from bs4 import BeautifulSoup

from app.models import ProductOffer, StoreInfo
from app.scrapers.base import BaseStoreScraper, calc_discount, make_offer_id, parse_cop_price
from app.scrapers.browser_pool import browser_pool


class FalabellaScraper(BaseStoreScraper):
    store = StoreInfo(
        id="falabella",
        name="Falabella",
        domain="www.falabella.com.co",
        color="#84cc16",
        badge_bg="rgba(132, 204, 22, 0.16)",
        card_name="CMR Falabella",
    )
    base_url = "https://www.falabella.com.co"

    async def search(
        self,
        query: str,
        max_results: int = 10,
        use_playwright: bool = True,
        timeout: float = 25.0,
    ) -> tuple[list[ProductOffer], str]:
        q_enc = urllib.parse.quote(query)
        api_url = f"{self.base_url}/s/browse/v1/search/co?Ntt={q_enc}&page=1"
        html_url = f"{self.base_url}/falabella-co/search?Ntt={q_enc}"

        async with self.create_http_client(
            timeout=min(timeout, 15.0),
            extra_headers={
                "Referer": f"{self.base_url}/falabella-co",
                "Origin": self.base_url,
            },
        ) as client:
            # 1) Falabella Browse JSON API
            try:
                resp = await client.get(api_url)
                if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
                    data = resp.json()
                    results = ((data.get("data") or {}).get("results")) or data.get("results") or []
                    if results:
                        offers = self._parse_falabella_items(results)
                        if offers:
                            return offers[:max_results], "falabella-api"
            except Exception:
                pass

            # 2) Falabella HTML __NEXT_DATA__
            try:
                resp = await client.get(html_url)
                if resp.status_code == 200:
                    html_utf8 = resp.content.decode("utf-8", errors="replace")
                    offers = self._parse_next_data(html_utf8)
                    if offers:
                        return offers[:max_results], "next-data"
            except Exception:
                pass

        # 3) Playwright fallback
        if use_playwright:
            offers = await self._search_via_playwright(html_url, max_results, timeout)
            if offers:
                return offers[:max_results], "playwright"

        return [], "falabella"

    def _parse_next_data(self, html: str) -> list[ProductOffer]:
        soup = BeautifulSoup(html, "lxml")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            return []
        try:
            data = json.loads(script.string)
            page_props = ((data.get("props") or {}).get("pageProps")) or {}
            results = page_props.get("results") or (page_props.get("searchData") or {}).get("results") or []
            if results:
                return self._parse_falabella_items(results)
        except Exception:
            pass
        return []

    def _parse_falabella_items(self, items: list[dict[str, Any]]) -> list[ProductOffer]:
        offers: list[ProductOffer] = []
        for item in items:
            try:
                title = (item.get("displayName") or item.get("title") or "").strip()
                if not title:
                    continue
                brand = (item.get("brand") or "").strip()
                raw_url = item.get("url") or ""
                if not raw_url:
                    pid = item.get("productId") or item.get("skuId")
                    if pid:
                        raw_url = f"/falabella-co/product/{pid}"
                    else:
                        continue
                url = raw_url if raw_url.startswith("http") else f"{self.base_url}{raw_url if raw_url.startswith('/') else '/' + raw_url}"

                # Parse Falabella prices array: cmrPrice, eventPrice, internetPrice, normalPrice
                prices_list = item.get("prices") or []
                internet_price: Optional[float] = None
                normal_price: Optional[float] = None
                cmr_price: Optional[float] = None
                discount_badge: Optional[int] = None

                for p_obj in prices_list:
                    ptype = str(p_obj.get("type") or "").lower()
                    raw_vals = p_obj.get("price") or []
                    val_raw = raw_vals[0] if isinstance(raw_vals, list) and raw_vals else raw_vals
                    val = parse_cop_price(val_raw)
                    if not val:
                        continue
                    if "cmr" in ptype:
                        cmr_price = val
                    elif "event" in ptype or "internet" in ptype:
                        if internet_price is None or val < internet_price:
                            internet_price = val
                    elif "normal" in ptype:
                        normal_price = val
                    else:
                        if internet_price is None:
                            internet_price = val

                    disc_str = str(p_obj.get("discountBadge") or "")
                    if "%" in disc_str:
                        import re
                        m = re.search(r"(\d+)", disc_str)
                        if m:
                            discount_badge = int(m.group(1))

                main_price = internet_price or normal_price or cmr_price
                if not main_price:
                    continue

                list_price = normal_price if (normal_price and normal_price > main_price) else None
                discount = calc_discount(main_price, list_price) or discount_badge

                card_price = cmr_price if (cmr_price and cmr_price < main_price) else None
                card_text = "Tarjeta CMR" if card_price else None

                # Badges / shipping
                badges: list[str] = []
                for b in item.get("badges") or []:
                    lbl = b.get("label") or b.get("text") if isinstance(b, dict) else str(b)
                    if lbl and len(lbl) <= 36:
                        badges.append(lbl.strip())

                shipping = None
                for key in ("shipping", "deliveryBadges"):
                    if item.get(key):
                        shipping = "Envío disponible"
                        break

                imgs = item.get("mediaUrls") or item.get("images") or []
                image_url = imgs[0] if isinstance(imgs, list) and imgs else item.get("imageUrl")

                offers.append(
                    ProductOffer(
                        id=make_offer_id(self.store.id, url, title),
                        store_id=self.store.id,
                        store_name=self.store.name,
                        store_color=self.store.color,
                        title=title,
                        brand=brand,
                        price=main_price,
                        list_price=list_price,
                        discount_percent=discount,
                        card_price=card_price,
                        card_promo_text=card_text,
                        promo_badges=badges[:4],
                        shipping_info=shipping,
                        url=url,
                        image_url=image_url,
                        sku=str(item.get("skuId") or item.get("productId") or "") or None,
                        seller=item.get("sellerName") or "Falabella",
                    )
                )
            except Exception:
                continue
        return offers

    async def _search_via_playwright(self, html_url: str, max_results: int, timeout: float) -> list[ProductOffer]:
        captured: list[dict[str, Any]] = []
        async with browser_pool.new_page() as page:
            if page is None:
                return []

            async def on_response(response):
                try:
                    if "/s/browse/v1/search" in response.url and response.status == 200:
                        data = await response.json()
                        res = ((data.get("data") or {}).get("results")) or data.get("results") or []
                        if res:
                            captured.extend(res)
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                await page.goto(html_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                await page.wait_for_timeout(3800)
            except Exception:
                pass

            if captured:
                return self._parse_falabella_items(captured)[:max_results]

            html = await page.content()
            return self._parse_next_data(html)[:max_results]
