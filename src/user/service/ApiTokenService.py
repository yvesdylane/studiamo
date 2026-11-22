"""Personal access keys (Settings > AI), used by AI apps over MCP.

    plain, key = ApiTokenService(db).create(user_id, "Claude on my laptop")   # show `plain` once
    ApiTokenService(db).list(user_id)
    ApiTokenService(db).revoke(user_id, key.id)
    user_id = ApiTokenService(db).authenticate("stm_...")                    # None if unknown / revoked

Keys look like "stm_" + 40 random characters. Only their SHA-256 is stored, so
a database leak does not reveal usable keys.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa

from src.base.ServiceErrors import NotFoundError, ServiceError
from src.db.DB import Db
from src.user.models.ApiToken import ApiToken

PREFIX = "stm_"
MAX_KEYS = 10


@dataclass(frozen=True)
class ApiTokenRead:
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class ApiTokenNotFoundError(NotFoundError):
    pass


class TooManyKeysError(ServiceError):
    pass


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def _read(t: ApiToken) -> ApiTokenRead:
    return ApiTokenRead(t.id, t.name, t.prefix, t.created_at, t.last_used_at)


class ApiTokenService:
    def __init__(self, db: Db) -> None:
        self._db = db

    def create(self, user_id: int, name: str) -> tuple[str, ApiTokenRead]:
        name = (name or "").strip()[:60] or "AI app"
        plain = PREFIX + secrets.token_urlsafe(30)
        with self._db.session() as session:
            active = session.scalar(sa.select(sa.func.count()).select_from(ApiToken)
                                    .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None)))
            if active >= MAX_KEYS:
                raise TooManyKeysError(f"At most {MAX_KEYS} keys: revoke one first")
            token = ApiToken(user_id=user_id, name=name, token_hash=_hash(plain), prefix=plain[:10])
            session.add(token)
            session.flush()
            session.refresh(token)
            return plain, _read(token)

    def list(self, user_id: int) -> list[ApiTokenRead]:
        with self._db.session() as session:
            rows = session.scalars(sa.select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
                                   .order_by(ApiToken.created_at.desc())).all()
            return [_read(t) for t in rows]

    def revoke(self, user_id: int, token_id: int) -> None:
        with self._db.session() as session:
            token = session.get(ApiToken, token_id)
            if token is None or token.user_id != user_id or token.revoked_at is not None:
                raise ApiTokenNotFoundError(f"Key {token_id} does not exist")
            token.revoked_at = datetime.now(UTC)

    def authenticate(self, plain: str) -> int | None:
        if not plain or not plain.startswith(PREFIX) or len(plain) > 100:
            return None
        with self._db.session() as session:
            token = session.scalars(sa.select(ApiToken).where(ApiToken.token_hash == _hash(plain),
                                                              ApiToken.revoked_at.is_(None))).first()
            if token is None:
                return None
            token.last_used_at = datetime.now(UTC)
            return token.user_id
