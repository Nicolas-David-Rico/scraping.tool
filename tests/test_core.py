import unittest

from app.models import ProductOffer, QueryIntent, StoreInfo
from app.scrapers.base import calc_discount, parse_cop_price
from app.services.ai_service import AIService
from app.services.comparator import filter_and_rank_offers
from app.services.firecrawl_service import FirecrawlService


class TestCoreLogic(unittest.TestCase):
    def test_parse_cop_price(self):
        self.assertEqual(parse_cop_price("$ 1.499.900"), 1499900.0)
        self.assertEqual(parse_cop_price("1.299.900,00"), 1299900.0)
        self.assertEqual(parse_cop_price(2599900.0), 2599900.0)
        self.assertEqual(parse_cop_price("849950"), 849950.0)
        self.assertIsNone(parse_cop_price("12"))
        self.assertIsNone(parse_cop_price(""))

    def test_calc_discount(self):
        self.assertEqual(calc_discount(1500000, 3000000), 50)
        self.assertIsNone(calc_discount(3000000, 2000000))
        self.assertIsNone(calc_discount(1500000, None))

    def test_local_budget_parsing(self):
        intent = AIService._parse_query_locally("Busco portátil Lenovo Ryzen 5 menos de 2.5 millones")
        self.assertEqual(intent.max_price, 2500000.0)
        self.assertIn("Lenovo", intent.search_query)

        intent2 = AIService._parse_query_locally("Freidora Oster hasta 400 mil")
        self.assertEqual(intent2.max_price, 400000.0)

    def test_filter_accessories_and_rank(self):
        intent = QueryIntent(raw_message="iPhone 15 128GB", search_query="iPhone 15 128GB")
        offers = [
            ProductOffer(
                id="1", store_id="exito", store_name="Éxito", store_color="#eab308",
                title="Funda protectora silicona para iPhone 15 128GB",
                price=45000, url="https://www.exito.com/funda/p"
            ),
            ProductOffer(
                id="2", store_id="alkosto", store_name="Alkosto", store_color="#ea580c",
                title="Celular Apple iPhone 15 128GB Negro",
                price=3299900, list_price=3899900, discount_percent=15,
                url="https://www.alkosto.com/iphone-15/p/1"
            ),
            ProductOffer(
                id="3", store_id="falabella", store_name="Falabella", store_color="#84cc16",
                title="Apple iPhone 15 128GB Azul",
                price=3199900, list_price=3899900, discount_percent=18, card_price=3099900,
                card_promo_text="Tarjeta CMR",
                url="https://www.falabella.com.co/iphone-15"
            ),
        ]
        ranked, stats, best, best_card, best_disc = filter_and_rank_offers(offers, intent)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(best.store_id, "falabella")
        self.assertEqual(best_card.card_price, 3099900)
        self.assertEqual(best_disc.discount_percent, 18)
        self.assertEqual(stats.savings_vs_max, 100000)

    def test_firecrawl_normalization(self):
        svc = FirecrawlService()
        store = StoreInfo(
            id="exito", name="Éxito", domain="www.exito.com",
            color="#eab308", badge_bg="rgba(234,179,8,0.16)", card_name="Tarjeta Éxito"
        )
        raw = [
            {
                "title": "Televisor Samsung 55 4K",
                "brand": "Samsung",
                "price_cop": 1649900,
                "list_price_cop": 2599900,
                "card_price_cop": 1599900,
                "url": "/televisor-samsung-55/p",
                "shipping_info": "Envío gratis",
            }
        ]
        offers = svc._normalize_extracted(raw, store, "https://www.exito.com", "https://www.exito.com/s")
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, 1649900.0)
        self.assertEqual(offers[0].discount_percent, 37)
        self.assertEqual(offers[0].card_price, 1599900.0)
        self.assertEqual(offers[0].url, "https://www.exito.com/televisor-samsung-55/p")


if __name__ == "__main__":
    unittest.main()
