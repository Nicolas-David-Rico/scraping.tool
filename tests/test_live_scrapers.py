import asyncio
import os
import unittest
from unittest.mock import patch

from app.scrapers.browser_pool import browser_pool
from app.scrapers.registry import list_stores, stream_store_searches


@unittest.skipUnless(
    os.getenv("RUN_LIVE_SCRAPER_TESTS") == "1",
    "Set RUN_LIVE_SCRAPER_TESTS=1 to run live store tests",
)
class TestLiveScrapers(unittest.TestCase):
    def test_all_seven_stores_live(self):
        async def _run_live():
            results = []
            try:
                async for offers, status in stream_store_searches(
                    query="Televisor Samsung 55",
                    max_results=5,
                    use_playwright=True,
                    timeout=20.0,
                ):
                    results.append((offers, status))
            finally:
                await browser_pool.close()
            return results

        with patch("app.scrapers.registry.firecrawl_service.is_primary_mode", return_value=False), patch(
            "app.scrapers.registry.firecrawl_service.is_enabled_for_fallback", return_value=False
        ):
            results = asyncio.run(_run_live())

        expected_ids = {s.id for s in list_stores()}
        observed_ids = {status.store_id for _, status in results}
        self.assertEqual(len(results), 7)
        self.assertEqual(observed_ids, expected_ids)

        print("\n=== LIVE 7-STORE SCRAPER REPORT ===")
        ok_stores = 0
        for offers, status in sorted(results, key=lambda item: item[1].store_id):
            top = f"${int(round(offers[0].price)):,} | {offers[0].title[:48]}" if offers else (status.error_message or "-")
            print(
                f"[{status.store_id:<12}] status={status.status:<7} count={status.count:<2} "
                f"method={status.method:<18} elapsed={status.elapsed_ms:>5}ms | {top}"
            )
            if status.status == "ok" and status.count > 0:
                ok_stores += 1
            for offer in offers:
                self.assertTrue(offer.title.strip())
                self.assertGreaterEqual(offer.price, 500)
                self.assertTrue(offer.url.startswith("http"))

        self.assertGreaterEqual(ok_stores, 1)


if __name__ == "__main__":
    unittest.main()
