import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.models import ProductOffer, StoreInfo
from app.scrapers.alkosto import AlkostoScraper
from app.scrapers.base import BaseStoreScraper
from app.scrapers.exito import ExitoScraper
from app.scrapers.falabella import FalabellaScraper
from app.scrapers.homecenter import HomecenterScraper
from app.scrapers.mercadolibre import MercadoLibreScraper
from app.scrapers.registry import stream_store_searches


class DummyScraper(BaseStoreScraper):
    def __init__(self, store: StoreInfo, mode: str = "ok"):
        self.store = store
        self.mode = mode

    async def search(self, query: str, max_results: int = 10, use_playwright: bool = True, timeout: float = 25.0):
        if self.mode == "ok":
            return [
                ProductOffer(
                    id=f"{self.store.id}-1",
                    store_id=self.store.id,
                    store_name=self.store.name,
                    store_color=self.store.color,
                    title=f"Producto {query}",
                    price=1500000,
                    url=f"https://{self.store.domain}/producto-1",
                )
            ], "dummy-api"
        if self.mode == "empty":
            return [], "dummy-api"
        if self.mode == "timeout":
            raise TimeoutError("timed out")
        if self.mode == "blocked":
            raise RuntimeError("403 blocked by cloudflare")
        raise ValueError("unexpected failure")


class TestScrapersOffline(unittest.TestCase):
    def test_vtex_standard_and_faststore_parsing(self):
        scraper = ExitoScraper()
        vtex_products = [
            {
                "productName": "Nevera Haceb 250L",
                "brand": "Haceb",
                "linkText": "nevera-haceb-250l",
                "clusterHighlights": {"1": "Envío gratis"},
                "items": [
                    {
                        "itemId": "1001",
                        "images": [{"imageUrl": "https://cdn.exito.com/nevera.jpg"}],
                        "sellers": [
                            {
                                "sellerName": "Éxito",
                                "commertialOffer": {
                                    "Price": 1800000,
                                    "ListPrice": 2250000,
                                    "AvailableQuantity": 0,
                                    "Teasers": [
                                        {
                                            "name": "10% con Tarjeta Éxito",
                                            "effects": {
                                                "parameters": [{"name": "PercentualDiscount", "value": "10"}]
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        ]
        offers = scraper._parse_vtex_products(vtex_products)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, 1800000.0)
        self.assertEqual(offers[0].list_price, 2250000.0)
        self.assertEqual(offers[0].discount_percent, 20)
        self.assertEqual(offers[0].card_price, 1620000.0)
        self.assertEqual(offers[0].card_promo_text, "Tarjeta Éxito")
        self.assertIn("Envío gratis", offers[0].promo_badges)
        self.assertFalse(offers[0].in_stock)
        self.assertEqual(offers[0].url, "https://www.exito.com/nevera-haceb-250l/p")

        faststore_nodes = [
            {
                "name": "Televisor LG 55 4K",
                "slug": "televisor-lg-55-4k",
                "sku": "LG55",
                "brand": {"name": "LG"},
                "image": [{"url": "https://cdn.exito.com/lg55.jpg"}],
                "offers": {
                    "lowPrice": 1999900,
                    "offers": [{"price": 1999900, "listPrice": 2499900, "seller": {"identifier": "exito"}}],
                },
            }
        ]
        fs_offers = scraper._parse_faststore_nodes(faststore_nodes)
        self.assertEqual(len(fs_offers), 1)
        self.assertEqual(fs_offers[0].price, 1999900.0)
        self.assertEqual(fs_offers[0].discount_percent, 20)
        self.assertEqual(fs_offers[0].url, "https://www.exito.com/televisor-lg-55-4k/p")

    def test_alkosto_algolia_and_html_parsing(self):
        scraper = AlkostoScraper()
        script_html = """
        <script>
          window.cfg = {
            algolia : {
              applicationId: 'QX54XYZ123',
              apiKey: 'abcdef1234567890abcdef1234567890',
              indexName: 'alkostoIndexAlgoliaPRD'
            }
          };
        </script>
        """
        cfg = scraper._extract_algolia_config(script_html)
        self.assertEqual(cfg, ("QX54XYZ123", "abcdef1234567890abcdef1234567890", "alkostoIndexAlgoliaPRD"))

        hits = [
            {
                "name_text_es": "Celular Samsung Galaxy A55",
                "lowestprice_double": 1499900,
                "pricevalue_cop_double": 1999900,
                "url_es_string": "/celular-samsung-a55/p/8801",
                "img-310wx310h_string": "/medias/a55.jpg",
                "marca_text": "Samsung",
                "enviogratis_boolean": True,
                "paymentpromotion_text_mv": ["0% interés Codensa"],
                "code_string": "8801",
            }
        ]
        offers = scraper._parse_algolia_hits(hits)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, 1499900.0)
        self.assertEqual(offers[0].list_price, 1999900.0)
        self.assertEqual(offers[0].discount_percent, 25)
        self.assertEqual(offers[0].url, "https://www.alkosto.com/celular-samsung-a55/p/8801")
        self.assertIn("Envío gratis", offers[0].promo_badges)

        ssr_html = """
        <ul>
          <li class="product__item">
            <a class="product__item__top__link" href="/tv-sony-50/p/12"></a>
            <h3 class="product__item__top__title">Televisor Sony 50</h3>
            <span class="product__price--discounts__price">$ 1.900.000</span>
            <span class="product__price--discounts__old">$ 2.500.000</span>
            <img src="/medias/sony.jpg" />
          </li>
        </ul>
        """
        html_offers = scraper._parse_html(ssr_html)
        self.assertEqual(len(html_offers), 1)
        self.assertEqual(html_offers[0].price, 1900000.0)
        self.assertEqual(html_offers[0].list_price, 2500000.0)

    def test_falabella_items_and_next_data(self):
        scraper = FalabellaScraper()
        items = [
            {
                "displayName": "Lavadora LG 18kg",
                "brand": "LG",
                "url": "/falabella-co/product/9988",
                "skuId": "9988",
                "prices": [
                    {"type": "normalPrice", "price": ["2.599.900"]},
                    {"type": "internetPrice", "price": ["2.099.900"]},
                    {"type": "cmrPrice", "price": ["1.899.900"]},
                ],
                "mediaUrls": ["https://falabella.scene7.com/is/image/FalabellaCO/9988"],
            }
        ]
        offers = scraper._parse_falabella_items(items)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, 2099900.0)
        self.assertEqual(offers[0].list_price, 2599900.0)
        self.assertEqual(offers[0].card_price, 1899900.0)
        self.assertEqual(offers[0].card_promo_text, "Tarjeta CMR")

        next_html = f"""
        <html><body>
          <script id="__NEXT_DATA__" type="application/json">
            {json.dumps({"props": {"pageProps": {"results": items}}})}
          </script>
        </body></html>
        """
        next_offers = scraper._parse_next_data(next_html)
        self.assertEqual(len(next_offers), 1)
        self.assertEqual(next_offers[0].card_price, 1899900.0)

    def test_homecenter_items_and_next_data(self):
        scraper = HomecenterScraper()
        items = [
            {
                "displayName": "Taladro Percutor Dewalt",
                "brand": "Dewalt",
                "productId": "554433",
                "prices": [
                    {"type": "normal", "priceWithoutFormatting": 699900},
                    {"type": "internet", "priceWithoutFormatting": 549900},
                    {"type": "cmr", "priceWithoutFormatting": 499900},
                ],
            }
        ]
        offers = scraper._parse_items(items)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, 549900.0)
        self.assertEqual(offers[0].card_price, 499900.0)
        self.assertIn("SodimacCO/554433", offers[0].image_url)

        next_html = f"""
        <html><body>
          <script id="__NEXT_DATA__" type="application/json">
            {json.dumps({"props": {"pageProps": {"searchProps": {"results": items}}}})}
          </script>
        </body></html>
        """
        next_offers = scraper._parse_next_data(next_html)
        self.assertEqual(len(next_offers), 1)
        self.assertEqual(next_offers[0].price, 549900.0)

    def test_mercadolibre_api_html_and_jsonld(self):
        scraper = MercadoLibreScraper()
        api_items = [
            {
                "id": "MCO123",
                "title": "Consola PlayStation 5 Slim",
                "price": 2399900,
                "original_price": 2799900,
                "permalink": "https://articulo.mercadolibre.com.co/MCO-123",
                "thumbnail": "http://http2.mlstatic.com/D_123-I.jpg",
                "shipping": {"free_shipping": True},
                "official_store_name": "Sony",
            }
        ]
        api_offers = scraper._parse_api_results(api_items)
        self.assertEqual(len(api_offers), 1)
        self.assertEqual(api_offers[0].image_url, "https://http2.mlstatic.com/D_123-O.jpg")
        self.assertEqual(api_offers[0].shipping_info, "Envío gratis")

        poly_html = """
        <div class="poly-card">
          <a class="poly-component__title" href="https://click1.mercadolibre.com.co/track">Audifonos Sony WH-1000XM5</a>
          <a href="https://www.mercadolibre.com.co/p/MCO999">Directo</a>
          <span class="andes-money-amount--previous"><span class="andes-money-amount__fraction">1.599.900</span></span>
          <div class="poly-price__current"><span class="andes-money-amount__fraction">1.199.900</span></div>
          <span class="poly-component__shipping">Envío gratis</span>
          <img class="poly-component__picture" src="data:image/gif;base64,R0lGOD" data-src="https://http2.mlstatic.com/xm5.jpg" />
        </div>
        """
        poly_offers = scraper._parse_html(poly_html)
        self.assertEqual(len(poly_offers), 1)
        self.assertEqual(poly_offers[0].url, "https://www.mercadolibre.com.co/p/MCO999")
        self.assertEqual(poly_offers[0].price, 1199900.0)
        self.assertEqual(poly_offers[0].list_price, 1599900.0)
        self.assertEqual(poly_offers[0].image_url, "https://http2.mlstatic.com/xm5.jpg")

        jsonld_html = """
        <script type="application/ld+json">
          {"@graph": [{"@type": "Product", "name": "Mouse Logitech MX", "offers": {"price": "389900", "url": "https://www.mercadolibre.com.co/MCO777"}}]}
        </script>
        """
        ld_offers = scraper._parse_html(jsonld_html)
        self.assertEqual(len(ld_offers), 1)
        self.assertEqual(ld_offers[0].price, 389900.0)

    def test_safe_search_status_classification(self):
        store = StoreInfo(id="demo", name="Demo", domain="demo.co", color="#111111", badge_bg="#222222")

        async def _run():
            _, s_ok = await DummyScraper(store, "ok").safe_search("tv")
            _, s_empty = await DummyScraper(store, "empty").safe_search("tv")
            _, s_timeout = await DummyScraper(store, "timeout").safe_search("tv")
            _, s_blocked = await DummyScraper(store, "blocked").safe_search("tv")
            _, s_error = await DummyScraper(store, "error").safe_search("tv")
            return s_ok, s_empty, s_timeout, s_blocked, s_error

        s_ok, s_empty, s_timeout, s_blocked, s_error = asyncio.run(_run())
        self.assertEqual(s_ok.status, "ok")
        self.assertEqual(s_ok.count, 1)
        self.assertEqual(s_empty.status, "empty")
        self.assertEqual(s_timeout.status, "timeout")
        self.assertEqual(s_blocked.status, "blocked")
        self.assertEqual(s_error.status, "error")

    def test_stream_store_searches_parallel_and_firecrawl_fallback(self):
        st1 = StoreInfo(id="s1", name="Tienda 1", domain="t1.co", color="#111111", badge_bg="#222222")
        st2 = StoreInfo(id="s2", name="Tienda 2", domain="t2.co", color="#333333", badge_bg="#444444")
        d1 = DummyScraper(st1, "ok")
        d2 = DummyScraper(st2, "empty")

        fc_offer = ProductOffer(
            id="fc-1",
            store_id="s2",
            store_name="Tienda 2",
            store_color="#333333",
            title="Producto Respaldo Firecrawl",
            price=1450000,
            url="https://t2.co/fc-1",
        )

        async def _collect():
            out = []
            async for offers, status in stream_store_searches("tv", max_results=5, use_playwright=False, timeout=10.0):
                out.append((offers, status))
            return out

        with patch("app.scrapers.registry.get_selected_scrapers", return_value=[d1, d2]), patch(
            "app.scrapers.registry.firecrawl_service.is_primary_mode", return_value=False
        ), patch(
            "app.scrapers.registry.firecrawl_service.is_enabled_for_fallback", return_value=True
        ), patch(
            "app.scrapers.registry.firecrawl_service.scrape_store_offers",
            new=AsyncMock(return_value=[fc_offer]),
        ):
            results = asyncio.run(_collect())

        by_id = {status.store_id: (offers, status) for offers, status in results}
        self.assertEqual(set(by_id.keys()), {"s1", "s2"})
        self.assertEqual(by_id["s1"][1].method, "dummy-api")
        self.assertEqual(by_id["s2"][1].status, "ok")
        self.assertEqual(by_id["s2"][1].method, "firecrawl-fallback")
        self.assertEqual(len(by_id["s2"][0]), 1)


if __name__ == "__main__":
    unittest.main()
