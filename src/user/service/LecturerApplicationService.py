"""Lecturer sign-up: the account is created at once, the 'lecturer' role only
when an admin approves the application.

    service = LecturerApplicationService(get_db())
    user, app = service.register(UserCreate(name="Dr. A", email="a@school.edu", password="...",
                                            accepted_terms=True),
                                 LecturerApplicationCreate(institution="IUT", courses_taught="HCI"))
    service.for_user(user.id)              # LecturerApplicationRead | None
    service.list(status=LecturerApplicationStatus.PENDING)
    service.approve(app.id, reviewer_id=admin.id)   # adds UserRole.LECTURER
    service.reject(app.id, reviewer_id=admin.id)

Until approval the user is a plain student, so every lecturer-only check
(has_role(LECTURER)) keeps them out. Who may approve is the route's job.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.base.Paging import DEFAULT_PAGE_SIZE, clamp
from src.base.ServiceErrors import constraint_name, reraise
from src.db.DB import Db
from src.user.dto.LecturerApplicationDto import LecturerApplicationCreate, LecturerApplicationRead
from src.user.dto.UserDto import UserCreate, UserRead
from src.user.dto.UserIdentityDto import UserIdentityCreate
from src.user.models.LecturerApplication import LecturerApplication, LecturerApplicationStatus
from src.user.models.User import User, UserRole
from src.user.models.UserIdentity import UserIdentity
from src.user.service.LecturerApplicationErrors import (
    LecturerApplicationExistsError,
    LecturerApplicationNotFoundError,
    LecturerApplicationReviewedError,
)
from src.user.service.UserErrors import UserNotFoundError
from src.user.service.UserService import UserService, drop_unverified_account, new_user

UNIQUE_USER_CONSTRAINT = "lecturer_applications_user_id_key"


class LecturerApplicationService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # ------------------------------------------------------------- create
    def register(
        self,
        account: UserCreate,
        data: LecturerApplicationCreate,
        identity: UserIdentityCreate | None = None,
    ) -> tuple[UserRead, LecturerApplicationRead]:
        """New account + pending application (+ a linked provider identity, e.g.
        Google), in one transaction: all or nothing."""
        user = new_user(account, verified=identity is not None)  # a provider identity = verified email
        try:
            with self._db.session() as session:
                drop_unverified_account(session, user.email)
                session.add(user)
                session.flush()
                if identity is not None:
                    session.add(UserIdentity(user_id=user.id, provider=identity.provider.lower(),
                                             provider_uid=identity.provider_uid))
                application = LecturerApplication(user_id=user.id, **data.model_dump())
                session.add(application)
                session.flush()
                session.refresh(user)
                session.refresh(application)
                return UserRead.model_validate(user), LecturerApplicationRead.model_validate(application)
        except IntegrityError as exc:
            reraise(exc, UserService._translate(exc, email=user.email, player_name=account.player_name,
                                                provider=identity.provider if identity else None))

    def apply(self, user_id: int, data: LecturerApplicationCreate) -> LecturerApplicationRead:
        """An existing user (e.g. a student) asks to become a lecturer."""
        try:
            with self._db.session() as session:
                if session.get(User, user_id) is None:
                    raise UserNotFoundError(user_id)
                application = LecturerApplication(user_id=user_id, **data.model_dump())
                session.add(application)
                session.flush()
                session.refresh(application)
                return LecturerApplicationRead.model_validate(application)
        except IntegrityError as exc:
            error = LecturerApplicationExistsError(user_id) if constraint_name(exc) == UNIQUE_USER_CONSTRAINT else None
            reraise(exc, error)

    # --------------------------------------------------------------- read
    def get(self, application_id: int) -> LecturerApplicationRead:
        with self._db.session() as session:
            return LecturerApplicationRead.model_validate(self._find(session, application_id))

    def for_user(self, user_id: int) -> LecturerApplicationRead | None:
        with self._db.session() as session:
            row = session.scalars(
                sa.select(LecturerApplication).where(LecturerApplication.user_id == user_id)
            ).first()
            return LecturerApplicationRead.model_validate(row) if row is not None else None

    def list(
        self,
        status: LecturerApplicationStatus | None = LecturerApplicationStatus.PENDING,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[LecturerApplicationRead]:
        """Oldest first, so the queue is reviewed in order. status=None lists all."""
        limit, offset = clamp(limit, offset)
        query = sa.select(LecturerApplication)
        if status is not None:
            query = query.where(LecturerApplication.status == status)
        with self._db.session() as session:
            rows = session.scalars(
                query.order_by(LecturerApplication.created_at, LecturerApplication.id).limit(limit).offset(offset)
            ).all()
            return [LecturerApplicationRead.model_validate(r) for r in rows]

    # ------------------------------------------------------------- review
    def approve(self, application_id: int, reviewer_id: int) -> LecturerApplicationRead:
        return self._review(application_id, reviewer_id, LecturerApplicationStatus.APPROVED)

    def reject(self, application_id: int, reviewer_id: int) -> LecturerApplicationRead:
        return self._review(application_id, reviewer_id, LecturerApplicationStatus.REJECTED)

    def _review(
        self, application_id: int, reviewer_id: int, status: LecturerApplicationStatus
    ) -> LecturerApplicationRead:
        with self._db.session() as session:
            application = self._find(session, application_id, for_update=True)
            if application.status != LecturerApplicationStatus.PENDING:
                raise LecturerApplicationReviewedError(application_id, application.status.value)
            if session.get(User, reviewer_id) is None:
                raise UserNotFoundError(reviewer_id)
            application.status = status
            application.reviewed_by = reviewer_id
            application.reviewed_at = sa.func.now()
            if status == LecturerApplicationStatus.APPROVED:
                user = session.get(User, application.user_id, with_for_update=True)
                if UserRole.LECTURER not in user.roles:
                    user.roles = [*user.roles, UserRole.LECTURER]
            session.flush()
            session.refresh(application)
            return LecturerApplicationRead.model_validate(application)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _find(session: Session, application_id: int, *, for_update: bool = False) -> LecturerApplication:
        row = session.get(LecturerApplication, application_id, with_for_update=for_update)
        if row is None:
            raise LecturerApplicationNotFoundError(application_id)
        return row
