"""Authentication: scrypt password hashes, HMAC-signed session cookies, login throttling."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, WebSocket

from .config import BOOTSTRAP_PASSWORD, BOOTSTRAP_USER, PANEL_DIR
from .models import User
from .store import JsonStore

COOKIE = "minenode_session"
SESSION_TTL = 14 * 24 * 3600

_users = JsonStore(PANEL_DIR / "users.json", dict)
_secret_file = PANEL_DIR / "secret.key"
_attempts: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))


def _secret() -> bytes:
    if not _secret_file.exists():
        _secret_file.parent.mkdir(parents=True, exist_ok=True)
        _secret_file.write_bytes(secrets.token_bytes(32))
        _secret_file.chmod(0o600)
    return _secret_file.read_bytes()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def users() -> dict[str, User]:
    return {k: User.model_validate(v) for k, v in _users.data.items()}


def setup_required() -> bool:
    return not _users.data


def create_user(username: str, password: str) -> User:
    username = username.strip()
    if not username or len(username) > 32 or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise HTTPException(400, "Nazwa użytkownika: 1–32 znaki (litery, cyfry, _ - .)")
    if len(password) < 8:
        raise HTTPException(400, "Hasło musi mieć co najmniej 8 znaków")
    if username.lower() in {u.lower() for u in _users.data}:
        raise HTTPException(409, "Taki użytkownik już istnieje")
    user = User(username=username, password_hash=hash_password(password))
    _users.data[username] = user.model_dump()
    _users.save()
    return user


def set_password(username: str, password: str) -> None:
    if username not in _users.data:
        raise HTTPException(404, "Nie ma takiego użytkownika")
    if len(password) < 8:
        raise HTTPException(400, "Hasło musi mieć co najmniej 8 znaków")
    _users.data[username]["password_hash"] = hash_password(password)
    _users.data[username]["token_version"] = _users.data[username].get("token_version", 0) + 1
    _users.save()


def delete_user(username: str) -> None:
    if username not in _users.data:
        raise HTTPException(404, "Nie ma takiego użytkownika")
    if len(_users.data) <= 1:
        raise HTTPException(400, "Nie można usunąć ostatniego użytkownika")
    _users.data.pop(username)
    _users.save()


def bootstrap() -> None:
    if setup_required() and BOOTSTRAP_USER and BOOTSTRAP_PASSWORD:
        create_user(BOOTSTRAP_USER, BOOTSTRAP_PASSWORD)


def check_rate_limit(ip: str) -> None:
    q = _attempts[ip]
    now = time.time()
    while q and now - q[0] > 300:
        q.popleft()
    if len(q) >= 8:
        raise HTTPException(429, "Zbyt wiele prób logowania. Spróbuj ponownie za kilka minut.")


def login(username: str, password: str, ip: str) -> str:
    check_rate_limit(ip)
    raw = _users.data.get(username)
    if not raw or not verify_password(password, raw["password_hash"]):
        _attempts[ip].append(time.time())
        raise HTTPException(401, "Nieprawidłowy login lub hasło")
    _attempts.pop(ip, None)
    return make_token(username)


def make_token(username: str) -> str:
    tv = _users.data.get(username, {}).get("token_version", 0)
    payload = base64.urlsafe_b64encode(json.dumps({"u": username, "exp": int(time.time()) + SESSION_TTL, "v": tv}).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        return None
    if data.get("exp", 0) < time.time():
        return None
    user = _users.data.get(data.get("u"))
    if not user or user.get("token_version", 0) != data.get("v", 0):
        return None
    return data["u"]


def require_user(request: Request) -> str:
    user = verify_token(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Wymagane logowanie")
    return user


def ws_user(ws: WebSocket) -> str | None:
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    if origin and host and origin.split("://", 1)[-1] != host:
        return None
    return verify_token(ws.cookies.get(COOKIE))
