"""Guests: people who join an exam with its code, their name and email, and
no account. Their email is checked with a 6-digit code before they start.

    guests = GuestService(db)
    guest, code = guests.request_code(exam_id, "Amina", "amina@example.com")   # email `code` to them
    guest = guests.verify(exam_id, "amina@example.com", "123456")              # -> verified guest

Codes are stored hashed, expire after CODE_MINUTES, allow MAX_TRIES wrong
tries, and can be sent again after RESEND_SECONDS. A guest gets no exp or rank.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from src.base.ServiceErrors import InvalidOperationError, NotFoundError
from src.db.DB import Db
from src.exam.models.ExamGuest import ExamGuest

CODE_MINUTES = 15
MAX_TRIES = 5
RESEND_SECONDS = 60


class GuestError(InvalidOperationError):
    """Shown to the guest as it is."""


class GuestNotFoundError(NotFoundError):
    pass


@dataclass(frozen=True)
class GuestRead:
    id: int
    exam_id: int
    email: str
    name: str
    verified: bool
    results_sent_at: datetime | None


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _read(g: ExamGuest) -> GuestRead:
    return GuestRead(g.id, g.exam_id, g.email, g.name, g.verified_at is not None, g.results_sent_at)


class GuestService:
    def __init__(self, db: Db) -> None:
        self._db = db

    def request_code(self, exam_id: int, name: str, email: str) -> tuple[GuestRead, str]:
        """A new 6-digit code for this guest (created on first use). GuestError if
        one was sent less than RESEND_SECONDS ago."""
        name, email = (name or "").strip()[:100], (email or "").strip().lower()[:255]
        if not name or "@" not in email:
            raise GuestError("Enter your name and a valid email address.")
        now = datetime.now(UTC)
        code = f"{secrets.randbelow(10 ** 6):06d}"
        with self._db.session() as session:
            g = session.scalars(sa.select(ExamGuest).where(ExamGuest.exam_id == exam_id, ExamGuest.email == email)
                                .with_for_update()).first()
            if g is None:
                g = ExamGuest(exam_id=exam_id, email=email, name=name)
                session.add(g)
            elif g.code_sent_at and now - g.code_sent_at < timedelta(seconds=RESEND_SECONDS):
                raise GuestError("A code was just sent. Check your inbox, or ask again in a minute.")
            g.name = name
            g.code_hash, g.code_expires_at, g.code_tries, g.code_sent_at = _hash(code), now + timedelta(minutes=CODE_MINUTES), 0, now
            session.flush()
            return _read(g), code

    def verify(self, exam_id: int, email: str, code: str) -> GuestRead:
        email, code = (email or "").strip().lower(), "".join(ch for ch in (code or "") if ch.isdigit())
        now = datetime.now(UTC)
        with self._db.session() as session:
            g = session.scalars(sa.select(ExamGuest).where(ExamGuest.exam_id == exam_id, ExamGuest.email == email)
                                .with_for_update()).first()
            if g is None or not g.code_hash:
                raise GuestError("Ask for a code first.")
            if g.code_expires_at is None or g.code_expires_at < now:
                raise GuestError("This code has expired. Ask for a new one.")
            if g.code_tries >= MAX_TRIES:
                raise GuestError("Too many wrong codes. Ask for a new one.")
            if not secrets.compare_digest(_hash(code), g.code_hash):
                g.code_tries += 1
                session.flush()
                left = MAX_TRIES - g.code_tries
                raise GuestError(f"That code is not right. {left} tr{'ies' if left != 1 else 'y'} left." if left else
                                 "Too many wrong codes. Ask for a new one.")
            g.verified_at, g.code_hash = now, None     # a code works once
            return _read(g)

    def get(self, guest_id: int) -> GuestRead:
        with self._db.session() as session:
            g = session.get(ExamGuest, guest_id)
            if g is None:
                raise GuestNotFoundError(f"Guest {guest_id} does not exist")
            return _read(g)

    def mark_results_sent(self, guest_id: int) -> None:
        with self._db.session() as session:
            g = session.get(ExamGuest, guest_id)
            if g is not None:
                g.results_sent_at = datetime.now(UTC)
