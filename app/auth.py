from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Optional

from app.config import DATA_DIR

USERS_FILE = DATA_DIR / "users.json"
SECRET_FILE = DATA_DIR / "secret.key"

PBKDF2_ITERATIONS = 310_000
SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 horas
COOKIE_NAME = "ofertaco_session"

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 300

_failed: dict[str, list[float]] = {}


def _load_secret() -> bytes:
    env = os.getenv("SECRET_KEY", "").strip()
    if env:
        return env.encode("utf-8")
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    key = secrets.token_bytes(48)
    SECRET_FILE.write_bytes(key)
    return key


_SECRET = _load_secret()


def _hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def _read_user() -> Optional[dict]:
    if not USERS_FILE.exists():
        return None
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("username") else None
    except Exception:
        return None


def setup_required() -> bool:
    return _read_user() is None


def validate_password_strength(password: str) -> Optional[str]:
    if len(password) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "La contraseña debe incluir letras y números."
    return None


def validate_username(username: str) -> Optional[str]:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", username):
        return "Usuario: 3-32 caracteres (letras, números, _ . -)."
    return None


def _save_user(username: str, password: str) -> None:
    salt = secrets.token_bytes(16)
    record = {
        "username": username,
        "salt": salt.hex(),
        "iterations": PBKDF2_ITERATIONS,
        "hash": _hash_password(password, salt),
    }
    USERS_FILE.write_text(json.dumps(record, indent=2), encoding="utf-8")


def create_initial_user(username: str, password: str) -> Optional[str]:
    """Crea el usuario único. Devuelve mensaje de error o None si fue exitoso."""
    if not setup_required():
        return "Ya existe un usuario configurado."
    err = validate_username(username) or validate_password_strength(password)
    if err:
        return err
    _save_user(username, password)
    return None


def bootstrap_from_env() -> None:
    """Para hosting: crea el usuario inicial desde APP_USERNAME / APP_PASSWORD si aún no existe."""
    user = os.getenv("APP_USERNAME", "").strip()
    pwd = os.getenv("APP_PASSWORD", "")
    if user and pwd and setup_required():
        if not validate_username(user) and not validate_password_strength(pwd):
            _save_user(user, pwd)


def verify_credentials(username: str, password: str) -> bool:
    rec = _read_user()
    # Comparación en tiempo constante aunque el usuario no exista
    salt = bytes.fromhex(rec["salt"]) if rec else b"\x00" * 16
    iters = int(rec.get("iterations", PBKDF2_ITERATIONS)) if rec else PBKDF2_ITERATIONS
    candidate = _hash_password(password, salt, iters)
    expected = rec["hash"] if rec else "0" * 64
    user_ok = bool(rec) and hmac.compare_digest(rec["username"], username)
    pass_ok = hmac.compare_digest(candidate, expected)
    return user_ok and pass_ok


def change_password(username: str, current: str, new: str) -> Optional[str]:
    if not verify_credentials(username, current):
        return "La contraseña actual es incorrecta."
    err = validate_password_strength(new)
    if err:
        return err
    _save_user(username, new)
    return None


# ---- Rate limiting ----
def is_locked(ip: str) -> int:
    """Devuelve segundos restantes de bloqueo (0 si no está bloqueado)."""
    now = time.time()
    attempts = [t for t in _failed.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _failed[ip] = attempts
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        return int(LOCKOUT_SECONDS - (now - attempts[0]))
    return 0


def register_failure(ip: str) -> None:
    _failed.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    _failed.pop(ip, None)


# ---- Sesiones firmadas (HMAC) ----
def create_session_token(username: str) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    payload = base64.urlsafe_b64encode(f"{username}|{expires}".encode("utf-8")).decode("ascii")
    sig = hmac.new(_SECRET, payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> Optional[str]:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(_SECRET, payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        username, expires = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8").rsplit("|", 1)
        if int(expires) < time.time():
            return None
        rec = _read_user()
        if not rec or rec["username"] != username:
            return None
        return username
    except Exception:
        return None
