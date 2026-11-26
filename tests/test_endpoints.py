import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, config
from app.main import app
from app.models import ProductOffer, StoreSearchStatus
from app.services.history import history_manager


class TestEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp_path = Path(self.tmpdir.name)

        self._orig_settings = dict(config.settings_manager._data)
        self._orig_history_path = history_manager.path
        self._orig_history_items = list(history_manager._items)

        def _restore_state():
            config.settings_manager._data = dict(self._orig_settings)
            history_manager.path = self._orig_history_path
            history_manager._items = list(self._orig_history_items)

        self.addCleanup(_restore_state)

        history_manager.path = tmp_path / "history.json"
        history_manager._items = []
        config.settings_manager._data = {
            "ai_provider": "gemini",
            "gemini_api_key": "",
            "gemini_model": "gemini-2.5-flash",
            "openai_api_key": "",
            "openai_model": "gpt-4o-mini",
            "firecrawl_api_key": "",
            "firecrawl_mode": "off",
            "use_playwright_fallback": False,
            "store_timeout_seconds": 15,
            "max_results_per_store": 10,
        }

        self.patches = [
            patch.object(auth, "USERS_FILE", tmp_path / "users.json"),
            patch.object(auth, "SECRET_FILE", tmp_path / "secret.key"),
            patch.object(auth, "_SECRET", b"endpoint-test-secret-key-fixed-value"),
            patch.object(auth, "_failed", {}),
            patch.object(config, "SETTINGS_FILE", tmp_path / "settings.json"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _setup_authenticated_client(self, client: TestClient, username: str = "admin", password: str = "Clave1234"):
        r = client.post("/api/auth/setup", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200)
        return r

    def test_auth_lifecycle_and_lockout(self):
        with TestClient(app) as client:
            st = client.get("/api/auth/status")
            self.assertEqual(st.status_code, 200)
            self.assertEqual(
                st.json(),
                {"authenticated": False, "username": None, "setup_required": True},
            )

            setup_resp = self._setup_authenticated_client(client, "admin_co", "Clave1234")
            self.assertIn(auth.COOKIE_NAME, setup_resp.cookies)

            st2 = client.get("/api/auth/status")
            self.assertEqual(st2.status_code, 200)
            self.assertEqual(
                st2.json(),
                {"authenticated": True, "username": "admin_co", "setup_required": False},
            )

            out_resp = client.post("/api/auth/logout")
            self.assertEqual(out_resp.status_code, 200)
            self.assertEqual(client.get("/api/stores").status_code, 401)

            for _ in range(auth.MAX_FAILED_ATTEMPTS):
                bad = client.post("/api/auth/login", json={"username": "admin_co", "password": "WrongPassword1"})
                self.assertEqual(bad.status_code, 401)

            locked = client.post("/api/auth/login", json={"username": "admin_co", "password": "WrongPassword1"})
            self.assertEqual(locked.status_code, 429)

            auth._failed.clear()
            ok_login = client.post("/api/auth/login", json={"username": "admin_co", "password": "Clave1234"})
            self.assertEqual(ok_login.status_code, 200)

            chg = client.post(
                "/api/auth/change-password",
                json={"current_password": "Clave1234", "new_password": "NuevaClave567"},
            )
            self.assertEqual(chg.status_code, 200)
            self.assertTrue(chg.json()["ok"])

    def test_stores_and_settings_and_lock(self):
        with TestClient(app) as client:
            self._setup_authenticated_client(client)

            stores_resp = client.get("/api/stores")
            self.assertEqual(stores_resp.status_code, 200)
            stores = stores_resp.json()
            self.assertEqual(len(stores), 7)
            self.assertEqual(
                {s["id"] for s in stores},
                {"exito", "alkosto", "falabella", "jumbo", "olimpica", "homecenter", "mercadolibre"},
            )

            cfg_resp = client.get("/api/settings")
            self.assertEqual(cfg_resp.status_code, 200)
            cfg_data = cfg_resp.json()
            self.assertIn("gemini_configured", cfg_data)
            self.assertIn("gemini_key_preview", cfg_data)
            self.assertNotIn("gemini_api_key", cfg_data)

            upd = client.post(
                "/api/settings",
                json={
                    "ai_provider": "gemini",
                    "gemini_api_key": "AIzaSecretTestKey1234",
                    "gemini_model": "gemini-2.5-flash",
                    "max_results_per_store": 8,
                },
            )
            self.assertEqual(upd.status_code, 200)
            upd_json = upd.json()
            self.assertTrue(upd_json["gemini_configured"])
            self.assertEqual(upd_json["gemini_key_preview"], "•••1234")
            self.assertEqual(upd_json["max_results_per_store"], 8)

            # Sending masked preview must NOT overwrite stored key
            upd2 = client.post(
                "/api/settings",
                json={
                    "ai_provider": "gemini",
                    "gemini_api_key": "•••1234",
                    "max_results_per_store": 6,
                },
            )
            self.assertEqual(upd2.status_code, 200)
            self.assertEqual(config.settings_manager._data["gemini_api_key"], "AIzaSecretTestKey1234")
            self.assertEqual(upd2.json()["max_results_per_store"], 6)

            with patch("app.main.LOCK_SETTINGS", True):
                locked = client.post("/api/settings", json={"max_results_per_store": 5})
                self.assertEqual(locked.status_code, 403)

    def test_chat_stream_history_followup_and_csv_export(self):
        async def fake_stream_store_searches(**kwargs):
            yield (
                [
                    ProductOffer(
                        id="alk-1",
                        store_id="alkosto",
                        store_name="Alkosto",
                        store_color="#ea580c",
                        title="Televisor Samsung 55 Crystal UHD",
                        brand="Samsung",
                        price=1899900,
                        list_price=2499900,
                        discount_percent=24,
                        url="https://www.alkosto.com/tv-samsung-55/p/1",
                        seller="Alkosto",
                    )
                ],
                StoreSearchStatus(
                    store_id="alkosto",
                    store_name="Alkosto",
                    store_color="#ea580c",
                    status="ok",
                    count=1,
                    elapsed_ms=120,
                    method="algolia-api",
                ),
            )
            yield (
                [
                    ProductOffer(
                        id="fal-1",
                        store_id="falabella",
                        store_name="Falabella",
                        store_color="#84cc16",
                        title="Televisor Samsung 55 pulgadas 4K",
                        brand="Samsung",
                        price=1799900,
                        list_price=2399900,
                        discount_percent=25,
                        card_price=1699900,
                        card_promo_text="Tarjeta CMR",
                        url="https://www.falabella.com.co/falabella-co/product/123",
                        seller="Falabella",
                    )
                ],
                StoreSearchStatus(
                    store_id="falabella",
                    store_name="Falabella",
                    store_color="#84cc16",
                    status="ok",
                    count=1,
                    elapsed_ms=150,
                    method="falabella-api",
                ),
            )

        with TestClient(app) as client:
            self._setup_authenticated_client(client)

            with patch("app.main.stream_store_searches", side_effect=fake_stream_store_searches):
                events = []
                with client.stream(
                    "POST",
                    "/api/chat/stream",
                    json={"message": "Televisor Samsung 55", "stores": ["alkosto", "falabella"]},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
                    for line in resp.iter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))

            stages = [e["stage"] for e in events]
            self.assertEqual(
                stages,
                ["understanding", "intent_ready", "store_done", "store_done", "analyzing", "complete"],
            )
            comparison = events[-1]["comparison"]
            search_id = comparison["search_id"]
            self.assertEqual(len(comparison["offers"]), 2)
            self.assertEqual(comparison["best_offer"]["store_id"], "falabella")

            hist_list = client.get("/api/history")
            self.assertEqual(hist_list.status_code, 200)
            self.assertTrue(any(item["search_id"] == search_id for item in hist_list.json()))

            hist_item = client.get(f"/api/history/{search_id}")
            self.assertEqual(hist_item.status_code, 200)
            self.assertEqual(hist_item.json()["search_id"], search_id)
            self.assertEqual(client.get("/api/history/noexiste").status_code, 404)

            followup = client.post(
                "/api/chat/followup",
                json={"search_id": search_id, "question": "¿Cuál es más barato?", "history": []},
            )
            self.assertEqual(followup.status_code, 200)
            self.assertIn("Falabella", followup.json()["answer"])
            self.assertEqual(
                client.post(
                    "/api/chat/followup",
                    json={"search_id": "noexiste", "question": "Hola", "history": []},
                ).status_code,
                404,
            )

            csv_resp = client.get(f"/api/export/{search_id}")
            self.assertEqual(csv_resp.status_code, 200)
            self.assertIn("text/csv", csv_resp.headers.get("content-type", ""))
            csv_text = csv_resp.content.decode("utf-8")
            self.assertTrue(csv_text.startswith("\ufeff"))
            self.assertIn("Ranking,Tienda,Producto", csv_text)
            self.assertIn("Televisor Samsung 55 pulgadas 4K", csv_text)

            del_resp = client.delete(f"/api/history/{search_id}")
            self.assertEqual(del_resp.status_code, 200)
            self.assertEqual(del_resp.json(), {"deleted": True})
            self.assertEqual(client.delete(f"/api/history/{search_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
