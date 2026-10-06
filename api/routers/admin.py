import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from api.security import ROLES, hash_password, invalidate_user, owner_only
from db.connection import get_supabase_client

router = APIRouter(prefix="/api/users", tags=["users"])


class NewUser(BaseModel):
    email: str
    name: str
    role: str
    property_ids: list[str] = []


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    property_ids: list[str] | None = None


def _temp_password() -> str:
    return secrets.token_urlsafe(9)


def _validate_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of {list(ROLES)}")


def _set_properties(db: Client, user_id: str, property_ids: list[str]) -> None:
    db.table("user_properties").delete().eq("user_id", user_id).execute()
    if property_ids:
        db.table("user_properties").insert(
            [{"user_id": user_id, "property_id": p} for p in property_ids]
        ).execute()


@router.get("")
def list_users(user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client)):
    users = (
        db.table("app_users")
        .select("id, email, name, role, active, created_at")
        .order("created_at")
        .execute()
        .data
    )
    links = db.table("user_properties").select("user_id, property_id").execute().data
    by_user: dict[str, list[str]] = {}
    for l in links:
        by_user.setdefault(l["user_id"], []).append(l["property_id"])
    return [{**u, "property_ids": by_user.get(u["id"], [])} for u in users]


@router.post("")
def create_user(
    body: NewUser, user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client)
):
    _validate_role(body.role)
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Enter a valid email")
    if db.table("app_users").select("id").eq("email", email).execute().data:
        raise HTTPException(409, "That email already has an account")
    password = _temp_password()
    created = (
        db.table("app_users")
        .insert(
            {
                "email": email, "name": body.name.strip(), "role": body.role,
                "password_hash": hash_password(password), "must_change_password": True,
            }
        )
        .execute()
        .data[0]
    )
    _set_properties(db, created["id"], body.property_ids)
    # Shown once — only the hash is stored.
    return {"id": created["id"], "temporary_password": password}


@router.patch("/{user_id}")
def update_user(
    user_id: str, body: UserUpdate,
    user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client),
):
    rows = db.table("app_users").select("id, role, active").eq("id", user_id).execute().data
    if not rows:
        raise HTTPException(404, "User not found")
    target = rows[0]
    if body.role is not None:
        _validate_role(body.role)

    # Never leave the app without an active owner.
    losing_owner = target["role"] == "owner" and (
        (body.role is not None and body.role != "owner") or body.active is False
    )
    if losing_owner:
        others = (
            db.table("app_users").select("id").eq("role", "owner").eq("active", True)
            .neq("id", user_id).execute().data
        )
        if not others:
            raise HTTPException(409, "There must be at least one active owner")

    updates = {k: v for k, v in body.model_dump(exclude={"property_ids"}).items() if v is not None}
    if updates:
        db.table("app_users").update(updates).eq("id", user_id).execute()
    if body.property_ids is not None:
        _set_properties(db, user_id, body.property_ids)
    invalidate_user(user_id)
    return {"ok": True}


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: str, user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client)
):
    if not db.table("app_users").select("id").eq("id", user_id).execute().data:
        raise HTTPException(404, "User not found")
    password = _temp_password()
    db.table("app_users").update(
        {"password_hash": hash_password(password), "must_change_password": True}
    ).eq("id", user_id).execute()
    invalidate_user(user_id)
    return {"temporary_password": password}
