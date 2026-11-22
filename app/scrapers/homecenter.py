from __future__ import annotations

import json
import urllib.parse
from typing import Any, Optional
from bs4 import BeautifulSoup

from app.models import ProductOffer, StoreInfo
from app.scrapers.base import BaseStoreScraper, calc_discount, make_offer_id, parse_cop_price
from app.scrapers.browser_pool import browser_pool


class HomecenterScraper(BaseStoreScraper):
    store = StoreInfo(
        id="homecenter",
        name="Homecenter",
        domain="www.homecenter.com.co",
        color="#2563eb",
        badge_bg="rgba(37, 99, 235, 0.16)",
        card_name="CMR / Banco Falabella",
    )
    base_url = "https://www.homecenter.com.co"

    async def search(
        self,
        query: str,
        max_results: int = 10,
        use_playwright: bool = True,
        timeout: float = 25.0,
    ) -> tuple[list[ProductOffer], str]:
        q_enc = urllib.parse.quote(query)
        api_urls = [
            f"{self.base_url}/s/search/v1/soco?q={q_enc}&currentpage=1",
            f"{self.base_url}/s/search/v1/soco/?q={q_enc}&currentpage=1&priceGroup=10",
        ]
        html_url = f"{self.base_url}/homecenter-co/search/?Ntt={q_enc}"

        async with self.create_http_client(
            timeout=min(timeout, 15.0),
            extra_headers={
                "Referer": f"{self.base_url}/homecenter-co/",
                "Origin": self.base_url,
            },
        ) as client:
            for api_url in api_urls:
                try:
                    resp = await client.get(api_url)
                    if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
                        data = resp.json()
                        results = ((data.get("data") or {}).get("results")) or data.get("results") or []
                        if results:
                            offers = self._parse_items(results)
                            if offers:
                                return offers[:max_results], "homecenter-api"
                except Exception:
                    continue

            try:
                resp = await client.get(html_url)
                if resp.status_code == 200:
                    html_utf8 = resp.content.decode("utf-8", errors="replace")
                    offers = self._parse_next_data(html_utf8)
                    if offers:
                        return offers[:max_results], "next-data"
            except Exception:
                pass

        if use_playwright:
            offers = await self._search_via_playwright(html_url, max_results, timeout)
            if offers:
                return offers[:max_results], "playwright"

        return [], "homecenter"

    def _parse_next_data(self, html: str) -> list[ProductOffer]:
        soup = BeautifulSoup(html, "lxml")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            return []
        try:
            data = json.loads(script.string)
            props = ((data.get("props") or {}).get("pageProps")) or {}
            search_props = props.get("searchProps") or props
            results = (
                search_props.get("results")
                or (search_props.get("searchData") or {}).get("results")
                or []
            )
            if results:
                return self._parse_items(results)
        except Exception:
            pass
        return []

    def _parse_items(self, items: list[dict[str, Any]]) -> list[ProductOffer]:
        offers: list[ProductOffer] = []
        for item in items:
            try:
                title = (item.get("displayName") or item.get("name") or item.get("title") or "").strip()
                if not title:
                    continue
                brand = (item.get("brand") or "").strip()
                pid = str(item.get("productId") or item.get("skuId") or "").strip()
                raw_url = item.get("url") or (f"/homecenter-co/product/{pid}/" if pid else "")
                if not raw_url:
                    continue
                url = raw_url if raw_url.startswith("http") else f"{self.base_url}{raw_url if raw_url.startswith('/') else '/' + raw_url}"

                prices_list = item.get("prices") or []
                main_price: Optional[float] = None
                normal_price: Optional[float] = None
                cmr_price: Optional[float] = None

                for p_obj in prices_list:
                    ptype = str(p_obj.get("type") or "").lower()
                    raw_val = p_obj.get("priceWithoutFormatting") or p_obj.get("price")
                    if isinstance(raw_val, list) and raw_val:
                        raw_val = raw_val[0]
                    val = parse_cop_price(raw_val)
                    if not val:
                        continue
                    if "cmr" in ptype:
                        cmr_price = val
                    elif "normal" in ptype:
                        normal_price = val
                    elif "event" in ptype or "internet" in ptype or "offer" in ptype:
                        if main_price is None or val < main_price:
                            main_price = val
                    else:
                        if main_price is None:
                            main_price = val

                main_price = main_price or normal_price or cmr_price
                if not main_price:
                    continue
                list_price = normal_price if (normal_price and normal_price > main_price) else None
                card_price = cmr_price if (cmr_price and cmr_price < main_price) else None

                imgs = item.get("mediaUrls") or item.get("images") or []
                image_url = imgs[0] if isinstance(imgs, list) and imgs else item.get("imageUrl")
                if not image_url and pid:
                    image_url = f"https://homecenterco.scene7.com/is/image/SodimacCO/{pid}?fmt=webp&fit=fit,1&wid=400&hei=400"

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
                        discount_percent=calc_discount(main_price, list_price),
                        card_price=card_price,
                        card_promo_text="Tarjeta CMR" if card_price else None,
                        url=url,
                        image_url=image_url,
                        sku=pid or None,
                        seller=item.get("sellerName") or "Homecenter",
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
                    if "/s/search/" in response.url and response.status == 200:
                        data = await response.json()
                        res = ((data.get("data") or {}).get("results")) or data.get("results") or []
                        if res:
                            captured.extend(res)
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                await page.goto(html_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                await page.wait_for_timeout(3500)
            except Exception:
                pass

            if captured:
                return self._parse_items(captured)[:max_results]

            html = await page.content()
            return self._parse_next_data(html)[:max_results]
