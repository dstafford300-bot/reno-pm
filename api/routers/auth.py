from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from supabase import Client

from api.security import (
    SESSION_COOKIE,
    SESSION_DAYS,
    any_user,
    check_login_allowed,
    create_session_token,
    current_user,
    hash_password,
    invalidate_user,
    record_login_failure,
    verify_password,
)
from db.connection import get_supabase_client

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


def capabilities(role: str) -> dict:
    """What the UI may show. The API enforces every one of these itself —
    this is only so the front end can hide controls a user can't use."""
    return {
        "view_budget": role in ("owner", "pm"),
        "edit_budget": role == "owner",
        "view_costs": role in ("owner", "pm"),
        "edit_schedule": role in ("owner", "pm"),
        "propose_changes": role == "contractor",
        "approve_changes": role == "owner",
        "view_journal": role in ("owner", "pm"),
        "view_materials": role in ("owner", "pm"),
        "upload_sow": role == "owner",
        "manage_users": role == "owner",
        "manage_projects": role == "owner",
    }


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "must_change_password": user["must_change_password"],
        "capabilities": capabilities(user["role"]),
    }


@router.post("/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Client = Depends(get_supabase_client),
):
    email = body.email.strip().lower()
    throttle_key = f"{request.client.host if request.client else '?'}:{email}"
    check_login_allowed(throttle_key)

    rows = db.table("app_users").select("*").eq("email", email).execute().data
    user = rows[0] if rows else None
    # Always run a hash comparison so response time doesn't reveal whether
    # the email exists.
    ok = verify_password(
        body.password, user["password_hash"] if user else hash_password("x")
    )
    if not user or not ok or not user["active"]:
        record_login_failure(throttle_key)
        raise HTTPException(401, "Wrong email or password")

    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user["id"], user["role"]),
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https",
        samesite="lax",
        path="/",
    )
    return public_user(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(any_user)):
    return public_user(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordBody,
    user: dict = Depends(current_user),
    db: Client = Depends(get_supabase_client),
):
    if len(body.new_password) < 10:
        raise HTTPException(422, "Use at least 10 characters")
    row = db.table("app_users").select("password_hash").eq("id", user["id"]).execute().data[0]
    if not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(401, "Current password is wrong")
    db.table("app_users").update(
        {"password_hash": hash_password(body.new_password), "must_change_password": False}
    ).eq("id", user["id"]).execute()
    invalidate_user(user["id"])
    return {"ok": True}
