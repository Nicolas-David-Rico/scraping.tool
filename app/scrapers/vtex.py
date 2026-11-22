from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Optional

from app.models import ProductOffer
from app.scrapers.base import BaseStoreScraper, calc_discount, make_offer_id, parse_cop_price
from app.scrapers.browser_pool import browser_pool


class VTEXStoreScraper(BaseStoreScraper):
    """Scraper reutilizable para grandes superficies colombianas basadas en VTEX (Éxito, Jumbo, Olímpica)."""

    base_url: str = ""
    search_page_template: str = ""
    card_keywords: tuple[str, ...] = ("tarjeta", "aliada", "cmr", "cencosud", "exito", "plata")

    async def search(
        self,
        query: str,
        max_results: int = 10,
        use_playwright: bool = True,
        timeout: float = 25.0,
    ) -> tuple[list[ProductOffer], str]:
        q_enc = urllib.parse.quote(query)
        count = max(max_results, 12)

        async with self.create_http_client(
            timeout=min(timeout, 15.0),
            extra_headers={
                "Referer": f"{self.base_url}/",
                "Origin": self.base_url,
                "Accept": "application/json, text/plain, */*",
            },
        ) as client:
            # 1) VTEX Intelligent Search API
            is_urls = [
                f"{self.base_url}/api/io/_v/api/intelligent-search/product_search/trade-policy/1?query={q_enc}&count={count}&page=1",
                f"{self.base_url}/_v/api/intelligent-search/product_search/trade-policy/1?query={q_enc}&count={count}&page=1",
            ]
            for url in is_urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
                        data = resp.json()
                        products = data.get("products") or []
                        if products:
                            offers = self._parse_vtex_products(products)
                            if offers:
                                return offers[:max_results], "vtex-is-api"
                except Exception:
                    continue

            # 2) VTEX Legacy Catalog Search API
            legacy_url = (
                f"{self.base_url}/api/catalog_system/pub/products/search"
                f"?ft={q_enc}&_from=0&_to={count - 1}"
            )
            try:
                resp = await client.get(legacy_url)
                if resp.status_code in (200, 206) and resp.text.lstrip().startswith("["):
                    products = resp.json()
                    if isinstance(products, list) and products:
                        offers = self._parse_vtex_products(products)
                        if offers:
                            return offers[:max_results], "vtex-catalog-api"
            except Exception:
                pass

        # 3) Playwright fallback (intercepts VTEX JSON / extracts __NEXT_DATA__ / JSON-LD / DOM)
        if use_playwright and self.search_page_template:
            offers = await self._search_via_playwright(query, max_results, timeout)
            if offers:
                return offers[:max_results], "playwright"

        return [], "vtex-api"

    def _parse_vtex_products(self, products: list[dict[str, Any]]) -> list[ProductOffer]:
        offers: list[ProductOffer] = []
        for prod in products:
            try:
                title = (prod.get("productName") or prod.get("productTitle") or "").strip()
                if not title:
                    continue

                brand = (prod.get("brand") or "").strip()
                link = prod.get("link") or ""
                link_text = prod.get("linkText") or ""
                if link and link.startswith("http"):
                    url = link
                elif link and link.startswith("/"):
                    url = f"{self.base_url}{link}"
                elif link_text:
                    url = f"{self.base_url}/{link_text.strip('/')}/p"
                else:
                    continue

                items = prod.get("items") or []
                best_item = None
                best_offer_obj = None
                best_seller_name = None
                best_price = None

                for item in items:
                    sellers = item.get("sellers") or []
                    for seller in sellers:
                        comm = seller.get("commertialOffer") or {}
                        p = parse_cop_price(comm.get("Price") or comm.get("SpotPrice"))
                        avail = comm.get("AvailableQuantity", 1)
                        if p and (avail is None or avail > 0):
                            if best_price is None or p < best_price:
                                best_price = p
                                best_item = item
                                best_offer_obj = comm
                                best_seller_name = seller.get("sellerName")

                if not best_price or not best_offer_obj:
                    # Try fallback even if AvailableQuantity == 0
                    for item in items:
                        for seller in item.get("sellers") or []:
                            comm = seller.get("commertialOffer") or {}
                            p = parse_cop_price(comm.get("Price") or comm.get("SpotPrice"))
                            if p:
                                best_price = p
                                best_item = item
                                best_offer_obj = comm
                                best_seller_name = seller.get("sellerName")
                                break
                        if best_price:
                            break

                if not best_price or not best_offer_obj:
                    continue

                list_price = parse_cop_price(
                    best_offer_obj.get("ListPrice") or best_offer_obj.get("PriceWithoutDiscount")
                )
                if list_price and list_price <= best_price:
                    list_price = None

                discount = calc_discount(best_price, list_price)

                # Extract card / teaser promos
                card_price, card_text, promo_badges = self._extract_vtex_promos(
                    prod, best_offer_obj, best_price
                )

                # Image
                image_url = None
                if best_item:
                    imgs = best_item.get("images") or []
                    if imgs and isinstance(imgs[0], dict):
                        image_url = imgs[0].get("imageUrl")

                sku = str(
                    (best_item or {}).get("itemId")
                    or prod.get("productReference")
                    or prod.get("productId")
                    or ""
                ).strip() or None

                in_stock = bool((best_offer_obj.get("AvailableQuantity") or 0) > 0)

                offers.append(
                    ProductOffer(
                        id=make_offer_id(self.store.id, url, title),
                        store_id=self.store.id,
                        store_name=self.store.name,
                        store_color=self.store.color,
                        title=title,
                        brand=brand,
                        price=best_price,
                        list_price=list_price,
                        discount_percent=discount,
                        card_price=card_price,
                        card_promo_text=card_text,
                        promo_badges=promo_badges[:4],
                        url=url,
                        image_url=image_url,
                        sku=sku,
                        seller=best_seller_name,
                        in_stock=in_stock,
                    )
                )
            except Exception:
                continue
        return offers

    def _extract_vtex_promos(
        self,
        prod: dict[str, Any],
        comm: dict[str, Any],
        base_price: float,
    ) -> tuple[Optional[float], Optional[str], list[str]]:
        card_price: Optional[float] = None
        card_text: Optional[str] = None
        badges: list[str] = []

        # VTEX Teasers / PromotionTeasers
        teasers = list(comm.get("Teasers") or []) + list(comm.get("PromotionTeasers") or [])
        for teaser in teasers:
            if not isinstance(teaser, dict):
                continue
            name = str(teaser.get("name") or teaser.get("Name") or "").strip()
            if not name:
                continue

            # Check for percentage or value in effects
            effects = teaser.get("effects") or teaser.get("Effects") or {}
            params = effects.get("parameters") or effects.get("Parameters") or []
            discount_pct = None
            discount_val = None
            for param in params:
                pname = str(param.get("name") or param.get("Name") or "").lower()
                pval = param.get("value") or param.get("Value")
                if "percent" in pname and pval:
                    try:
                        discount_pct = float(str(pval).replace(",", "."))
                    except Exception:
                        pass
                elif "nominal" in pname or "price" in pname:
                    discount_val = parse_cop_price(pval)

            name_lower = name.lower()
            is_card = any(k in name_lower for k in self.card_keywords)
            if is_card:
                if discount_pct and 1 <= discount_pct <= 70:
                    est = round(base_price * (1.0 - discount_pct / 100.0))
                    if card_price is None or est < card_price:
                        card_price = est
                        card_text = self.store.card_name or name[:45]
                elif discount_val and discount_val < base_price:
                    est = round(base_price - discount_val)
                    if est > 0 and (card_price is None or est < card_price):
                        card_price = est
                        card_text = self.store.card_name or name[:45]
                else:
                    if len(name) <= 45:
                        badges.append(name)
            else:
                if len(name) <= 45 and name not in badges:
                    badges.append(name)

        # Cluster highlights
        clusters = prod.get("clusterHighlights") or {}
        if isinstance(clusters, dict):
            for val in clusters.values():
                s = str(val).strip()
                if s and len(s) <= 36 and s not in badges:
                    badges.append(s)

        return card_price, card_text, badges

    async def _search_via_playwright(
        self, query: str, max_results: int, timeout: float
    ) -> list[ProductOffer]:
        captured_products: list[dict[str, Any]] = []
        search_url = self.search_page_template.format(q=urllib.parse.quote(query))

        async with browser_pool.new_page() as page:
            if page is None:
                return []

            async def on_response(response):
                try:
                    rurl = response.url.lower()
                    if any(k in rurl for k in ("intelligent-search", "product_search", "catalog_system", "graphql", "search")):
                        ct = response.headers.get("content-type", "")
                        if "json" in ct and response.status in (200, 206):
                            data = await response.json()
                            if isinstance(data, list) and data and isinstance(data[0], dict) and "productName" in data[0]:
                                captured_products.extend(data)
                            elif isinstance(data, dict):
                                prods = data.get("products")
                                if isinstance(prods, list) and prods:
                                    captured_products.extend(prods)
                                # FastStore GraphQL (Éxito)
                                edges = (
                                    ((data.get("data") or {}).get("search") or {})
                                    .get("products", {})
                                    .get("edges")
                                )
                                if isinstance(edges, list) and edges:
                                    captured_products.extend([e.get("node") for e in edges if isinstance(e, dict) and e.get("node")])
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                await page.wait_for_timeout(3800)
            except Exception:
                pass

            if captured_products:
                # Support both VTEX standard and FastStore GraphQL nodes
                vtex_prods = [p for p in captured_products if "productName" in p]
                if vtex_prods:
                    parsed = self._parse_vtex_products(vtex_prods)
                    if parsed:
                        return parsed[:max_results]
                faststore_prods = [p for p in captured_products if "offers" in p and "name" in p]
                if faststore_prods:
                    parsed = self._parse_faststore_nodes(faststore_prods)
                    if parsed:
                        return parsed[:max_results]

            # Fallback: extract JSON-LD or generic product cards from rendered DOM
            return await self._extract_dom_fallback(page, max_results)

    def _parse_faststore_nodes(self, nodes: list[dict[str, Any]]) -> list[ProductOffer]:
        offers: list[ProductOffer] = []
        for node in nodes:
            try:
                title = (node.get("name") or "").strip()
                slug = (node.get("slug") or "").strip("/")
                if not title or not slug:
                    continue
                url = f"{self.base_url}/{slug}/p" if not slug.startswith("http") else slug
                offers_obj = node.get("offers") or {}
                low = parse_cop_price(offers_obj.get("lowPrice"))
                offer_list = offers_obj.get("offers") or []
                list_price = None
                seller = None
                if offer_list and isinstance(offer_list[0], dict):
                    low = low or parse_cop_price(offer_list[0].get("price"))
                    list_price = parse_cop_price(offer_list[0].get("listPrice"))
                    seller_obj = offer_list[0].get("seller")
                    if isinstance(seller_obj, dict):
                        seller = seller_obj.get("identifier")
                if not low:
                    continue
                if list_price and list_price <= low:
                    list_price = None
                imgs = node.get("image") or []
                image_url = imgs[0].get("url") if imgs and isinstance(imgs[0], dict) else None
                brand_obj = node.get("brand") or {}
                brand = brand_obj.get("name", "") if isinstance(brand_obj, dict) else str(brand_obj)

                offers.append(
                    ProductOffer(
                        id=make_offer_id(self.store.id, url, title),
                        store_id=self.store.id,
                        store_name=self.store.name,
                        store_color=self.store.color,
                        title=title,
                        brand=brand,
                        price=low,
                        list_price=list_price,
                        discount_percent=calc_discount(low, list_price),
                        url=url,
                        image_url=image_url,
                        sku=str(node.get("sku") or "") or None,
                        seller=seller,
                    )
                )
            except Exception:
                continue
        return offers

    async def _extract_dom_fallback(self, page, max_results: int) -> list[ProductOffer]:
        try:
            raw_items = await page.evaluate(
                """() => {
                    const out = [];
                    // 1) JSON-LD ItemList / Product
                    document.querySelectorAll('script[type="application/ld+json"]').forEach(sc => {
                        try {
                            const j = JSON.parse(sc.textContent);
                            const list = j.itemListElement || (Array.isArray(j) ? j : [j]);
                            for (const el of list) {
                                const item = el.item || el;
                                if (item && item.name && item.offers) {
                                    const off = Array.isArray(item.offers) ? item.offers[0] : item.offers;
                                    out.push({
                                        title: item.name,
                                        url: item.url || el.url || '',
                                        price: off.lowPrice || off.price,
                                        listPrice: off.highPrice || null,
                                        image: Array.isArray(item.image) ? item.image[0] : (item.image?.url || item.image || ''),
                                        brand: typeof item.brand === 'object' ? item.brand?.name : (item.brand || '')
                                    });
                                }
                            }
                        } catch {}
                    });
                    if (out.length > 0) return out;

                    // 2) Generic product anchors with price
                    const cards = document.querySelectorAll('article, [data-testid*="product"], [class*="product-card"], [class*="ProductCard"]');
                    cards.forEach(card => {
                        const a = card.querySelector('a[href*="/p"], a[href]');
                        if (!a) return;
                        const text = card.innerText || '';
                        const prices = [...text.matchAll(/\\$\\s*([\\d\\.]{4,})/g)].map(m => m[1]);
                        if (!prices.length) return;
                        const titleEl = card.querySelector('h2, h3, [class*="name"], [class*="title"]');
                        const img = card.querySelector('img');
                        out.push({
                            title: (titleEl ? titleEl.innerText : a.title || a.innerText || '').trim().split('\\n')[0],
                            url: a.href,
                            price: prices[prices.length - 1],
                            listPrice: prices.length > 1 ? prices[0] : null,
                            image: img ? (img.src || img.getAttribute('data-src') || '') : '',
                            brand: ''
                        });
                    });
                    return out;
                }"""
            )
        except Exception:
            return []

        offers: list[ProductOffer] = []
        for item in raw_items or []:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            price = parse_cop_price(item.get("price"))
            if not title or not url or not price:
                continue
            if url.startswith("/"):
                url = f"{self.base_url}{url}"
            list_p = parse_cop_price(item.get("listPrice"))
            if list_p and list_p <= price:
                list_p = None
            offers.append(
                ProductOffer(
                    id=make_offer_id(self.store.id, url, title),
                    store_id=self.store.id,
                    store_name=self.store.name,
                    store_color=self.store.color,
                    title=title,
                    brand=(item.get("brand") or "").strip(),
                    price=price,
                    list_price=list_p,
                    discount_percent=calc_discount(price, list_p),
                    url=url,
                    image_url=item.get("image") or None,
                )
            )
            if len(offers) >= max_results:
                break
        return offers
