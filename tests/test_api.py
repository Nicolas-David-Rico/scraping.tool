import unittest
from fastapi.testclient import TestClient

from app.main import app


class TestAPI(unittest.TestCase):
    def test_protected_without_session(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.get("/login").status_code, 200)
            self.assertEqual(client.get("/api/stores").status_code, 401)
            self.assertEqual(client.get("/api/settings").status_code, 401)
            r = client.get("/", follow_redirects=False)
            self.assertIn(r.status_code, (302, 303, 307))
            self.assertEqual(r.headers["location"], "/login")
            self.assertEqual(r.headers.get("x-frame-options"), "DENY")


if __name__ == "__main__":
    unittest.main()
