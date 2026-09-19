"""Session and password helpers. Tokens stay in httpOnly cookies, never in JSON."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import secrets
import uuid

import bcrypt
from fastapi import Cookie, Header, HTTPException, Response

from config import COOKIE_SAMESITE, COOKIE_SECURE, SESSION_TTL_DAYS
from database import db
from models import User


def public_user(user_doc: Dict[str, Any]) -> User:
    """Build the public User model without leaking hashes or Google subject IDs."""
    created_at = user_doc.get("created_at")
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    return User(
        user_id=user_doc["user_id"],
        email=user_doc["email"],
        name=user_doc["name"],
        picture=user_doc.get("picture") or None,
        created_at=created_at,
        auth_provider="google" if user_doc.get("google_sub") else "local",
        has_password=bool(user_doc.get("password_hash")),
    )


async def get_current_user(
    session_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> User:
    token = session_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    user_doc = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user_doc:
        raise HTTPException(status_code=401, detail="User not found")
    return public_user(user_doc)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


async def issue_session(user_id: str, response: Response) -> str:
    """Create a session row and set the auth cookie. Token is opaque and random."""
    session_token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": (now + timedelta(days=SESSION_TTL_DAYS)).isoformat(),
        "created_at": now.isoformat(),
    })
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
    )
    return session_token


def auth_payload(user_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Public account fields. The session token stays in the httpOnly cookie only."""
    return public_user(user_doc).model_dump(mode="json")


async def upsert_google_user(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Create or attach a local CineMind user from a verified Google profile."""
    email = str(profile.get("email") or "").strip().lower()
    sub = str(profile.get("google_sub") or "").strip()
    if not email:
        raise ValueError("Google profile missing email")
    existing = None
    if sub:
        existing = await db.users.find_one({"google_sub": sub}, {"_id": 0})
    if not existing:
        existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        updates: Dict[str, Any] = {}
        if sub and existing.get("google_sub") != sub:
            updates["google_sub"] = sub
        if profile.get("name") and not existing.get("name"):
            updates["name"] = profile["name"]
        if profile.get("picture") and not existing.get("picture"):
            updates["picture"] = profile["picture"]
        if updates:
            await db.users.update_one({"user_id": existing["user_id"]}, {"$set": updates})
            existing.update(updates)
        return existing
    user_doc = {
        "user_id": f"user_{uuid.uuid4().hex[:12]}",
        "email": email,
        "name": (profile.get("name") or email.split("@")[0])[:80],
        "picture": profile.get("picture") or "",
        "google_sub": sub or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(dict(user_doc))
    return user_doc
