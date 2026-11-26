import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import auth


class TestAuthModule(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp_path = Path(self.tmpdir.name)

        self.patches = [
            patch.object(auth, "USERS_FILE", tmp_path / "users.json"),
            patch.object(auth, "SECRET_FILE", tmp_path / "secret.key"),
            patch.object(auth, "_SECRET", b"test-secret-key-for-unit-tests-only-32bytes"),
            patch.object(auth, "_failed", {}),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_username_and_password_validation(self):
        self.assertIsNotNone(auth.validate_username("ab"))
        self.assertIsNotNone(auth.validate_username("bad user"))
        self.assertIsNotNone(auth.validate_username("user@mail"))
        self.assertIsNone(auth.validate_username("admin_1.a-b"))

        self.assertIsNotNone(auth.validate_password_strength("Abc123"))
        self.assertIsNotNone(auth.validate_password_strength("SoloLetras"))
        self.assertIsNotNone(auth.validate_password_strength("123456789"))
        self.assertIsNone(auth.validate_password_strength("Clave1234"))

    def test_initial_setup_and_duplicate_rejection(self):
        self.assertTrue(auth.setup_required())
        err = auth.create_initial_user("tester", "Clave1234")
        self.assertIsNone(err)
        self.assertFalse(auth.setup_required())

        dup_err = auth.create_initial_user("otro", "Clave5678")
        self.assertIsNotNone(dup_err)

    def test_verify_and_change_password(self):
        self.assertIsNone(auth.create_initial_user("usuario1", "Clave1234"))
        self.assertTrue(auth.verify_credentials("usuario1", "Clave1234"))
        self.assertFalse(auth.verify_credentials("usuario1", "Incorrecta1"))
        self.assertFalse(auth.verify_credentials("inexistente", "Clave1234"))

        bad_cur = auth.change_password("usuario1", "Incorrecta1", "NuevaClave99")
        self.assertIsNotNone(bad_cur)

        weak_new = auth.change_password("usuario1", "Clave1234", "sololetras")
        self.assertIsNotNone(weak_new)

        ok = auth.change_password("usuario1", "Clave1234", "NuevaClave99")
        self.assertIsNone(ok)
        self.assertFalse(auth.verify_credentials("usuario1", "Clave1234"))
        self.assertTrue(auth.verify_credentials("usuario1", "NuevaClave99"))

    def test_bootstrap_from_env(self):
        with patch.dict(os.environ, {"APP_USERNAME": "envuser", "APP_PASSWORD": "EnvPass123"}):
            auth.bootstrap_from_env()
            self.assertFalse(auth.setup_required())
            self.assertTrue(auth.verify_credentials("envuser", "EnvPass123"))

        with patch.dict(os.environ, {"APP_USERNAME": "otheruser", "APP_PASSWORD": "OtherPass123"}):
            auth.bootstrap_from_env()
            self.assertTrue(auth.verify_credentials("envuser", "EnvPass123"))
            self.assertFalse(auth.verify_credentials("otheruser", "OtherPass123"))

    def test_session_tokens(self):
        self.assertIsNone(auth.create_initial_user("sesion_user", "Clave1234"))

        token = auth.create_session_token("sesion_user")
        self.assertEqual(auth.verify_session_token(token), "sesion_user")

        payload, sig = token.rsplit(".", 1)
        tampered = f"{payload}.{'0' * len(sig)}"
        self.assertIsNone(auth.verify_session_token(tampered))

        future_time = time.time() + auth.SESSION_TTL_SECONDS + 100
        with patch("app.auth.time.time", return_value=future_time):
            self.assertIsNone(auth.verify_session_token(token))

        other_token = auth.create_session_token("otro_usuario")
        self.assertIsNone(auth.verify_session_token(other_token))

    def test_rate_limiting(self):
        ip = "192.0.2.55"
        self.assertEqual(auth.is_locked(ip), 0)
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            auth.register_failure(ip)
        self.assertGreater(auth.is_locked(ip), 0)
        auth.clear_failures(ip)
        self.assertEqual(auth.is_locked(ip), 0)


if __name__ == "__main__":
    unittest.main()
