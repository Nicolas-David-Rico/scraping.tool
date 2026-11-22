from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any
from bs4 import BeautifulSoup

from app.models import ProductOffer, StoreInfo
from app.scrapers.base import BaseStoreScraper, calc_discount, make_offer_id, parse_cop_price
from app.scrapers.browser_pool import browser_pool


class MercadoLibreScraper(BaseStoreScraper):
    store = StoreInfo(
        id="mercadolibre",
        name="Mercado Libre",
        domain="www.mercadolibre.com.co",
        color="#f59e0b",
        badge_bg="rgba(245, 158, 11, 0.16)",
        card_name=None,
    )

    async def search(
        self,
        query: str,
        max_results: int = 10,
        use_playwright: bool = True,
        timeout: float = 25.0,
    ) -> tuple[list[ProductOffer], str]:
        q_enc = urllib.parse.quote(query)
        slug = urllib.parse.quote(re.sub(r"\s+", "-", query.strip()))
        api_url = f"https://api.mercadolibre.com/sites/MCO/search?q={q_enc}&limit={max(max_results, 12)}"
        html_url = f"https://listado.mercadolibre.com.co/{slug}"

        blocked_reason = None
        async with self.create_http_client(timeout=min(timeout, 15.0)) as client:
            # 1) Public ML API (fast when not rate-limited)
            try:
                resp = await client.get(api_url)
                if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
                    results = (resp.json() or {}).get("results") or []
                    if results:
                        offers = self._parse_api_results(results)
                        if offers:
                            return offers[:max_results], "ml-api"
                elif resp.status_code in (401, 403):
                    blocked_reason = "403 API pública restringida"
            except Exception:
                pass

            # 2) HTML listing scraping
            try:
                resp = await client.get(
                    html_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": "https://www.mercadolibre.com.co/",
                    },
                )
                if "account-verification" in str(resp.url):
                    blocked_reason = "Mercado Libre exige inicio de sesión / verificación de cuenta (blocked)"
                elif resp.status_code == 200:
                    offers = self._parse_html(resp.content.decode("utf-8", errors="replace"))
                    if offers:
                        return offers[:max_results], "html"
            except Exception:
                pass

        # 3) Playwright fallback (only if not already gated by login/account verification)
        if use_playwright and not (blocked_reason and "verificación" in blocked_reason):
            offers = await self._search_via_playwright(html_url, max_results, timeout)
            if offers:
                return offers[:max_results], "playwright"

        if blocked_reason:
            raise RuntimeError(blocked_reason)

        return [], "mercadolibre"

    def _parse_api_results(self, results: list[dict[str, Any]]) -> list[ProductOffer]:
        offers: list[ProductOffer] = []
        for item in results:
            try:
                title = (item.get("title") or "").strip()
                price = parse_cop_price(item.get("price"))
                url = item.get("permalink") or ""
                if not title or not price or not url:
                    continue
                list_price = parse_cop_price(item.get("original_price"))
                if list_price and list_price <= price:
                    list_price = None

                badges: list[str] = []
                shipping = item.get("shipping") or {}
                ship_txt = None
                if shipping.get("free_shipping"):
                    ship_txt = "Envío gratis"
                    badges.append("Envío gratis")
                if item.get("official_store_name"):
                    badges.append(f"Tienda oficial {item['official_store_name']}")

                thumb = item.get("thumbnail") or ""
                if thumb.startswith("http://"):
                    thumb = "https://" + thumb[7:]
                thumb = thumb.replace("-I.jpg", "-O.jpg")

                offers.append(
                    ProductOffer(
                        id=make_offer_id(self.store.id, url, title),
                        store_id=self.store.id,
                        store_name=self.store.name,
                        store_color=self.store.color,
                        title=title,
                        brand="",
                        price=price,
                        list_price=list_price,
                        discount_percent=calc_discount(price, list_price),
                        promo_badges=badges[:3],
                        shipping_info=ship_txt,
                        url=url,
                        image_url=thumb or None,
                        sku=item.get("id"),
                        seller=item.get("official_store_name") or (item.get("seller") or {}).get("nickname"),
                    )
                )
            except Exception:
                continue
        return offers

    def _parse_html(self, html: str) -> list[ProductOffer]:
        soup = BeautifulSoup(html, "lxml")
        offers: list[ProductOffer] = []

        # Modern Mercado Libre poly-card layout + classic ui-search-result
        cards = soup.select("li.ui-search-layout__item, div.poly-card, div.ui-search-result__wrapper")
        for card in cards:
            try:
                link_el = card.select_one(
                    "a.poly-component__title, a.ui-search-link__title-card, a.ui-search-item__group__element, h2 a, h3 a"
                )
                if not link_el:
                    continue
                title = link_el.get_text(" ", strip=True)
                url = link_el.get("href", "").strip()
                if not title or not url or "click1.mercadolibre" in url:
                    # Try finding direct product link inside card
                    alt = card.select_one("a[href*='mercadolibre.com.co/MCO'], a[href*='/p/MCO']")
                    if alt:
                        url = alt.get("href", "").strip()
                    if not url:
                        continue
                # Clean tracking query params while preserving core URL
                url = url.split("#")[0]

                # Current price (not previous struck-through price)
                price_container = card.select_one(
                    ".poly-price__current .andes-money-amount__fraction, "
                    ".ui-search-price__second-line .andes-money-amount__fraction, "
                    ".andes-money-amount:not(.andes-money-amount--previous) .andes-money-amount__fraction"
                )
                price = parse_cop_price(price_container.get_text(strip=True) if price_container else "")
                if not price:
                    continue

                prev_el = card.select_one(".andes-money-amount--previous .andes-money-amount__fraction")
                list_price = parse_cop_price(prev_el.get_text(strip=True) if prev_el else "")
                if list_price and list_price <= price:
                    list_price = None

                disc_el = card.select_one(".andes-money-amount__discount, .ui-search-price__discount")
                disc_pct = calc_discount(price, list_price)
                if not disc_pct and disc_el:
                    m = re.search(r"(\d+)", disc_el.get_text())
                    if m:
                        disc_pct = int(m.group(1))

                badges: list[str] = []
                ship_el = card.select_one(".poly-component__shipping, .ui-search-item__shipping")
                ship_txt = None
                if ship_el and "gratis" in ship_el.get_text(" ", strip=True).lower():
                    ship_txt = "Envío gratis"
                    badges.append("Envío gratis")

                seller_el = card.select_one(".poly-component__seller, .ui-search-official-store-label")
                seller = seller_el.get_text(" ", strip=True) if seller_el else None
                if seller:
                    seller = re.sub(r"^Por\s+", "", seller, flags=re.I).strip()

                img_el = card.select_one("img.poly-component__picture, img.ui-search-result-image__element, img")
                img = None
                if img_el:
                    img = img_el.get("data-src") or img_el.get("src")
                    if img and img.startswith("data:"):
                        img = img_el.get("data-src") or None

                offers.append(
                    ProductOffer(
                        id=make_offer_id(self.store.id, url, title),
                        store_id=self.store.id,
                        store_name=self.store.name,
                        store_color=self.store.color,
                        title=title,
                        price=price,
                        list_price=list_price,
                        discount_percent=disc_pct,
                        promo_badges=badges,
                        shipping_info=ship_txt,
                        url=url,
                        image_url=img,
                        seller=seller,
                    )
                )
            except Exception:
                continue

        if offers:
            return offers

        # JSON-LD fallback
        for sc in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(sc.get_text(strip=True))
                graph = data.get("@graph") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for node in graph or []:
                    if isinstance(node, dict) and node.get("@type") == "Product":
                        off = node.get("offers") or {}
                        price = parse_cop_price(off.get("price"))
                        url = off.get("url") or node.get("url") or ""
                        if price and url and node.get("name"):
                            offers.append(
                                ProductOffer(
                                    id=make_offer_id(self.store.id, url, node["name"]),
                                    store_id=self.store.id,
                                    store_name=self.store.name,
                                    store_color=self.store.color,
                                    title=node["name"].strip(),
                                    price=price,
                                    url=url,
                                    image_url=node.get("image") if isinstance(node.get("image"), str) else None,
                                )
                            )
            except Exception:
                continue

        return offers

    async def _search_via_playwright(self, html_url: str, max_results: int, timeout: float) -> list[ProductOffer]:
        async with browser_pool.new_page() as page:
            if page is None:
                return []
            try:
                await page.goto(html_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                await page.wait_for_timeout(3000)
                html = await page.content()
                return self._parse_html(html)[:max_results]
            except Exception:
                return []
