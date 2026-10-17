"""Tenant resolution.

Production flow: ``X-API-Key`` -> hash -> account. Keys are compared with
``hmac.compare_digest`` so a wrong key cannot be brute-forced by timing.

Demo flow: when ``ADSENTRY_DEMO=1`` the bundled dashboard may name an account
with ``X-Account-Id``. This exists so the UI has no embedded secret; it is off
by default in the Docker image and covered by a test.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from adsentry.config import Settings, get_settings
from adsentry.db import get_session
from adsentry.models import Account


def hash_api_key(raw: str) -> str:
    """Keys are stored hashed; a database leak must not leak live credentials."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _account_from_key(session: Session, raw_key: str) -> Account | None:
    digest = hash_api_key(raw_key)
    candidates = session.scalars(select(Account)).all()
    for account in candidates:
        if hmac.compare_digest(account.api_key_hash, digest):
            return account
    return None


def get_tenant(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_account_id: str | None = Header(default=None, alias="X-Account-Id"),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Account:
    if x_api_key:
        account = _account_from_key(session, x_api_key)
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
            )
        return account

    if settings.demo_mode and x_account_id:
        try:
            account_id = int(x_account_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="X-Account-Id must be an integer") from exc
        account = session.get(Account, account_id)
        if account is not None:
            return account
        raise HTTPException(status_code=404, detail="Account not found")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide X-API-Key (or enable demo mode and send X-Account-Id)",
    )


def require_account_scope(account: Account) -> int:
    """Single choke point for the tenant id used in queries."""
    return account.id
