"""Accounts, sessions and role/property access for the app API.

Own accounts rather than Supabase Auth: the app already talks to Supabase
with a service key from the server only, so a small `app_users` table with
scrypt-hashed passwords and a signed session cookie keeps auth entirely
under this code's control (no dashboard configuration to drift out of sync).
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict

import jwt
from fastapi import Depends, HTTPException, Request
from supabase import Client

from db.connection import get_supabase_client

SESSION_COOKIE = "reno_session"
SESSION_DAYS = 14
ROLES = ("owner", "pm", "contractor")

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, digest_b64 = stored.split("$")
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# ----------------------------------------------------------------- sessions
def _jwt_secret() -> str:
    secret = os.environ.get("APP_JWT_SECRET")
    if not secret:
        raise RuntimeError("APP_JWT_SECRET is not set")
    return secret


def create_session_token(user_id: str, role: str) -> str:
    return jwt.encode(
        {"sub": user_id, "role": role, "exp": int(time.time()) + SESSION_DAYS * 86400},
        _jwt_secret(),
        algorithm="HS256",
    )


# --------------------------------------------------------- login throttling
_failed: dict[str, list[float]] = defaultdict(list)
_WINDOW_S, _MAX_FAILS = 900, 8


def check_login_allowed(key: str) -> None:
    now = time.time()
    _failed[key] = [t for t in _failed[key] if now - t < _WINDOW_S]
    if len(_failed[key]) >= _MAX_FAILS:
        raise HTTPException(429, "Too many attempts — try again in a few minutes.")


def record_login_failure(key: str) -> None:
    _failed[key].append(time.time())


# -------------------------------------------------------------- user lookup
_user_cache: dict[str, tuple[float, dict]] = {}
_CACHE_S = 20


def _load_user(db: Client, user_id: str) -> dict | None:
    cached = _user_cache.get(user_id)
    if cached and time.time() - cached[0] < _CACHE_S:
        return cached[1]
    rows = (
        db.table("app_users")
        .select("id, email, name, role, active, must_change_password")
        .eq("id", user_id)
        .execute()
        .data
    )
    user = rows[0] if rows else None
    if user:
        _user_cache[user_id] = (time.time(), user)
    return user


def invalidate_user(user_id: str) -> None:
    _user_cache.pop(user_id, None)


def current_user(request: Request, db: Client = Depends(get_supabase_client)) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in")
    try:
        claims = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired — please sign in again")
    user = _load_user(db, claims["sub"])
    if not user or not user["active"]:
        raise HTTPException(401, "Account disabled")

    # Browsers send cookies on cross-site requests unless SameSite stops
    # them; as defence in depth, state-changing calls must come from this
    # site's own origin.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            raise HTTPException(403, "Cross-site request blocked")
    return user


def require_roles(*roles: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, "Your account can't do that")
        return user

    return dependency


owner_only = require_roles("owner")
owner_or_pm = require_roles("owner", "pm")
any_user = require_roles("owner", "pm", "contractor")


# ----------------------------------------------------------- property access
def accessible_property_ids(db: Client, user: dict) -> set[str] | None:
    """None means "all properties" (owner/pm); otherwise the contractor's set."""
    if user["role"] in ("owner", "pm"):
        return None
    rows = (
        db.table("user_properties")
        .select("property_id")
        .eq("user_id", user["id"])
        .execute()
        .data
    )
    return {r["property_id"] for r in rows}


def assert_property_access(db: Client, user: dict, property_id: str) -> None:
    allowed = accessible_property_ids(db, user)
    if allowed is not None and property_id not in allowed:
        raise HTTPException(404, "Property not found")  # don't reveal it exists


def can_see_costs(user: dict) -> bool:
    return user["role"] in ("owner", "pm")
