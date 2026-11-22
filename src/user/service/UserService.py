"""Accounts: sign-up, sign-in, profile, roles and external identities.

    service = UserService(get_db())
    user = service.create(UserCreate(name="Shin", email="a@b.com", password="secret123"))
    user = service.authenticate("a@b.com", "secret123")
    user = service.update(user.id, UserUpdate(player_name="shin"))
    user = service.set_roles(user.id, UserRolesUpdate(roles=[UserRole.STUDENT, UserRole.LECTURER]))
    user = service.sign_in_with_provider("google", "1234", name="Shin", email="a@b.com",
                                         email_verified=True)

Emails are stored lower-cased. Passwords are hashed with werkzeug (scrypt);
password_hash never leaves this service. A player_stats row is created for
every new user by a database trigger.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from src.base.Paging import DEFAULT_PAGE_SIZE, clamp
from src.base.ServiceErrors import constraint_name, reraise
from src.db.DB import Db
from src.user.dto.UserDto import UserCreate, UserPublic, UserRead, UserRolesUpdate, UserUpdate
from src.user.dto.UserIdentityDto import UserIdentityCreate, UserIdentityRead
from src.user.models.User import User, UserRole
from src.user.models.UserIdentity import UserIdentity
from src.user.service.UserErrors import (
    EmailNotVerifiedError,
    EmailTakenError,
    IdentityNotFoundError,
    IdentityTakenError,
    InvalidCredentialsError,
    LastLoginMethodError,
    PlayerNameTakenError,
    UserError,
    UserHasContentError,
    UserNotFoundError,
    VerificationLinkError,
)

UNIQUE_EMAIL_CONSTRAINT = "users_email_key"
UNIQUE_PLAYER_NAME_CONSTRAINT = "users_player_name_key"
UNIQUE_IDENTITY_CONSTRAINT = "user_identities_provider_provider_uid_key"
# users referenced without ON DELETE CASCADE: authored content blocks deletion
CONTENT_FK_CONSTRAINTS = {
    "courses_created_by_fkey",
    "questions_author_id_fkey",
    "exams_created_by_fkey",
}

# checked against when the email is unknown, so both failures take the same time
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


RESEND_COOLDOWN_SECONDS = 60


def new_user(data: UserCreate, *, verified: bool = False) -> User:
    """The (unsaved) User row for a sign-up; shared with LecturerApplicationService.
    verified=True when a provider (Google) already checked the email."""
    return User(
        name=data.name,
        player_name=data.player_name,
        email=_normalize_email(data.email),
        password_hash=generate_password_hash(data.password) if data.password else None,
        class_name=data.class_name,
        terms_accepted_at=sa.func.now() if data.accepted_terms else None,
        email_verified_at=sa.func.now() if verified else None,
    )


def drop_unverified_account(session: Session, email: str) -> None:
    """Free an email held by an account that never verified it.

    Someone can sign up with an address that is not theirs; that account can
    never log in. When the real owner signs up (or uses Google), the stale
    account is deleted so it cannot block them or keep a password they did not
    choose. Accounts with a linked identity or authored content are kept.
    """
    stale = session.scalars(
        sa.select(User).where(User.email == _normalize_email(email), User.email_verified_at.is_(None))
    ).first()
    if stale is None or stale.identities:
        return
    try:
        with session.begin_nested():
            session.delete(stale)
            session.flush()
    except IntegrityError:
        pass  # it authored something: leave it, the sign-up gets EmailTakenError


class UserService:
    def __init__(self, db: Db) -> None:
        self._db = db

    # ------------------------------------------------------------- create
    def create(self, data: UserCreate) -> UserRead:
        """Sign-up with a password. New users are plain students whose email is
        not verified yet (they cannot log in until it is)."""
        user = new_user(data)
        try:
            with self._db.session() as session:
                drop_unverified_account(session, user.email)
                session.add(user)
                session.flush()
                session.refresh(user)
                return UserRead.model_validate(user)
        except IntegrityError as exc:
            reraise(exc, self._translate(exc, email=user.email, player_name=data.player_name))

    # --------------------------------------------------------------- read
    def get(self, user_id: int) -> UserRead:
        with self._db.session() as session:
            return UserRead.model_validate(self._find(session, user_id))

    def get_public(self, user_id: int) -> UserPublic:
        with self._db.session() as session:
            return UserPublic.model_validate(self._find(session, user_id))

    def get_by_email(self, email: str) -> UserRead | None:
        with self._db.session() as session:
            user = self._by_email(session, email)
            return UserRead.model_validate(user) if user is not None else None

    def list(self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> list[UserRead]:
        """Users ordered by id. limit is capped at MAX_PAGE_SIZE."""
        limit, offset = clamp(limit, offset)
        with self._db.session() as session:
            users = session.scalars(sa.select(User).order_by(User.id).limit(limit).offset(offset)).all()
            return [UserRead.model_validate(u) for u in users]

    def count(self) -> int:
        with self._db.session() as session:
            return session.scalar(sa.select(sa.func.count()).select_from(User)) or 0

    def has_password(self, user_id: int) -> bool:
        """False for accounts that only sign in with a provider (Google)."""
        with self._db.session() as session:
            return self._find(session, user_id).password_hash is not None

    def search(self, text: str, limit: int = DEFAULT_PAGE_SIZE) -> list[UserRead]:
        """Users whose name or email contains text (case ignored), by id."""
        limit, _ = clamp(limit, 0)
        pattern = "%" + text.strip().lower().replace("%", r"\%").replace("_", r"\_") + "%"
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(User)
                .where(sa.or_(sa.func.lower(User.name).like(pattern), User.email.like(pattern)))
                .order_by(User.id)
                .limit(limit)
            ).all()
            return [UserRead.model_validate(u) for u in rows]

    def set_avatar(self, user_id: int, avatar_path: str | None) -> str | None:
        """Set (or clear, with None) the profile photo; returns the previous file name."""
        with self._db.session() as session:
            user = self._find(session, user_id)
            previous, user.avatar_path = user.avatar_path, avatar_path
            return previous

    def has_role(self, user_id: int, role: UserRole) -> bool:
        return role in self.get(user_id).roles

    # ------------------------------------------------------------- auth
    def authenticate(self, email: str, password: str) -> UserRead:
        """The user if email and password match, InvalidCredentialsError otherwise."""
        with self._db.session() as session:
            user = self._by_email(session, email)
            stored = user.password_hash if user is not None else None
            ok = check_password_hash(stored or _DUMMY_HASH, password)
            if user is None or stored is None or not ok:
                raise InvalidCredentialsError()
            if user.email_verified_at is None:
                raise EmailNotVerifiedError(user.id, user.email)
            return UserRead.model_validate(user)

    def change_password(self, user_id: int, current: str | None, new: str) -> None:
        """current must match unless the user has no password yet (external sign-in only)."""
        with self._db.session() as session:
            user = self._find(session, user_id)
            if user.password_hash is not None and not (
                current and check_password_hash(user.password_hash, current)
            ):
                raise InvalidCredentialsError()
            user.password_hash = generate_password_hash(UserUpdate(password=new).password)

    # ------------------------------------------------------------- update
    def update(self, user_id: int, data: UserUpdate) -> UserRead:
        """Partial update. player_name can be cleared with UserUpdate(player_name=None).

        A password sent here replaces the old one without checking it: use
        change_password for the "type your current password" flow.
        """
        email = _normalize_email(data.email) if data.email is not None else None
        try:
            with self._db.session() as session:
                user = self._find(session, user_id)
                if data.name is not None:
                    user.name = data.name
                if "player_name" in data.model_fields_set:
                    user.player_name = data.player_name
                if "class_name" in data.model_fields_set:
                    user.class_name = data.class_name
                if data.ranking_visibility is not None:
                    user.ranking_visibility = data.ranking_visibility
                if "accent_color" in data.model_fields_set:
                    user.accent_color = data.accent_color
                if data.theme is not None:
                    user.theme = data.theme
                if "secondary_color" in data.model_fields_set:
                    user.secondary_color = data.secondary_color
                if data.font is not None:
                    user.font = data.font
                if data.text_size is not None:
                    user.text_size = data.text_size
                if data.toast_enabled is not None:
                    user.toast_enabled = data.toast_enabled
                if data.toast_seconds is not None:
                    user.toast_seconds = data.toast_seconds
                if email is not None:
                    user.email = email
                if data.password is not None:
                    user.password_hash = generate_password_hash(data.password)
                session.flush()
                session.refresh(user)  # updated_at comes from a trigger
                return UserRead.model_validate(user)
        except IntegrityError as exc:
            reraise(exc, self._translate(exc, email=email, player_name=data.player_name))

    def set_roles(self, user_id: int, data: UserRolesUpdate) -> UserRead:
        """Replace the user's roles: student and/or lecturer (admins only: the route checks g.is_admin)."""
        with self._db.session() as session:
            user = self._find(session, user_id)
            user.roles = list(dict.fromkeys(data.roles))  # dedupe, keep order
            session.flush()
            session.refresh(user)
            return UserRead.model_validate(user)

    # ------------------------------------------------------------- delete
    def delete(self, user_id: int) -> None:
        """Delete an account with its attempts, marks and exp (ON DELETE CASCADE).

        Users who authored courses, questions or exams cannot be deleted
        (UserHasContentError): that content would lose its author.
        """
        try:
            with self._db.session() as session:
                session.delete(self._find(session, user_id))
                session.flush()
        except IntegrityError as exc:
            reraise(exc, self._translate(exc, user_id=user_id))

    # ---------------------------------------------- external identities
    def list_identities(self, user_id: int) -> list[UserIdentityRead]:
        with self._db.session() as session:
            user = self._find(session, user_id)
            return [UserIdentityRead.model_validate(i) for i in user.identities]

    def link_identity(self, user_id: int, data: UserIdentityCreate) -> UserIdentityRead:
        identity = UserIdentity(user_id=user_id, provider=data.provider.lower(), provider_uid=data.provider_uid)
        try:
            with self._db.session() as session:
                self._find(session, user_id)
                session.add(identity)
                session.flush()
                return UserIdentityRead.model_validate(identity)
        except IntegrityError as exc:
            reraise(exc, self._translate(exc, provider=identity.provider))

    def unlink_identity(self, user_id: int, identity_id: int) -> None:
        """Refuses to remove the only way left to sign in (no password, no other identity)."""
        with self._db.session() as session:
            user = self._find(session, user_id)
            identity = next((i for i in user.identities if i.id == identity_id), None)
            if identity is None:
                raise IdentityNotFoundError(identity_id)
            if user.password_hash is None and len(user.identities) == 1:
                raise LastLoginMethodError()
            session.delete(identity)

    def find_by_identity(self, provider: str, provider_uid: str) -> UserRead | None:
        with self._db.session() as session:
            user = self._by_identity(session, provider, provider_uid)
            return UserRead.model_validate(user) if user is not None else None

    def sign_in_with_provider(
        self,
        provider: str,
        provider_uid: str,
        *,
        name: str,
        email: str,
        email_verified: bool,
    ) -> UserRead:
        """OAuth callback: the linked user, else link by email, else sign up.

        An existing account is linked by email only when the provider says the
        email is verified; otherwise anyone could take over an account by
        registering its email with a provider. In that case EmailTakenError
        is raised and the user must sign in and link the provider themselves.
        """
        provider = provider.lower()
        email = _normalize_email(email)
        with self._db.session() as session:
            user = self._by_identity(session, provider, provider_uid)
            if user is not None:
                return UserRead.model_validate(user)

            user = self._by_email(session, email)
            if user is None:
                user = User(name=(name or email)[:100], email=email,
                            email_verified_at=sa.func.now() if email_verified else None)
                session.add(user)
                session.flush()
            elif not email_verified:
                raise EmailTakenError(email)
            elif user.email_verified_at is None:
                user.password_hash = None   # set by someone who never proved they own the email
                user.email_verified_at = sa.func.now()

            session.add(UserIdentity(user_id=user.id, provider=provider, provider_uid=provider_uid))
            session.flush()
            session.refresh(user)
            return UserRead.model_validate(user)

    def find_for_provider_login(
        self, provider: str, provider_uid: str, *, email: str, email_verified: bool
    ) -> UserRead | None:
        """Log-in with a provider, never creating an account.

        The user linked to this identity; else the account with this email if
        the provider verified it (the identity is linked to it now); else None,
        and the caller asks whether to sign up as a student or a lecturer.
        EmailTakenError if the email belongs to an account but is not verified.
        """
        provider = provider.lower()
        with self._db.session() as session:
            user = self._by_identity(session, provider, provider_uid)
            if user is not None:
                return UserRead.model_validate(user)
            user = self._by_email(session, email)
            if user is None:
                return None
            if not email_verified:
                raise EmailTakenError(_normalize_email(email))
            if user.email_verified_at is None:
                # the password was set by someone who never proved they own this
                # email: it may not be the owner, so it stops working
                user.password_hash = None
                user.email_verified_at = sa.func.now()
            session.add(UserIdentity(user_id=user.id, provider=provider, provider_uid=provider_uid))
            session.flush()
            session.refresh(user)
            return UserRead.model_validate(user)

    def create_with_identity(self, data: UserCreate, identity: UserIdentityCreate) -> UserRead:
        """Sign-up through a provider: the account and its linked identity
        together. The provider verified the email, so the account is verified."""
        user = new_user(data, verified=True)
        try:
            with self._db.session() as session:
                drop_unverified_account(session, user.email)
                session.add(user)
                session.flush()
                session.add(UserIdentity(user_id=user.id, provider=identity.provider.lower(),
                                         provider_uid=identity.provider_uid))
                session.flush()
                session.refresh(user)
                return UserRead.model_validate(user)
        except IntegrityError as exc:
            reraise(exc, self._translate(exc, email=user.email, player_name=data.player_name,
                                         provider=identity.provider))

    # ------------------------------------------------- email verification
    def mark_email_verified(self, user_id: int, email: str) -> UserRead:
        """The verification link was opened. email is the one the link was made
        for: if the account's email changed since, the link is refused."""
        with self._db.session() as session:
            user = session.get(User, user_id, with_for_update=True)
            if user is None or user.email != _normalize_email(email):
                raise VerificationLinkError()
            if user.email_verified_at is None:
                user.email_verified_at = sa.func.now()
                session.flush()
                session.refresh(user)
            return UserRead.model_validate(user)

    def claim_verification_send(self, email: str) -> UserRead | None:
        """The unverified account for email, if a verification email may be sent
        now (at most one per RESEND_COOLDOWN_SECONDS); None otherwise."""
        with self._db.session() as session:
            user = session.scalars(
                sa.update(User)
                .where(
                    User.email == _normalize_email(email),
                    User.email_verified_at.is_(None),
                    sa.or_(
                        User.verification_sent_at.is_(None),
                        User.verification_sent_at < sa.func.now() - sa.text(f"interval '{RESEND_COOLDOWN_SECONDS} seconds'"),
                    ),
                )
                .values(verification_sent_at=sa.func.now())
                .returning(User)
            ).first()
            return UserRead.model_validate(user) if user is not None else None

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _find(session: Session, user_id: int) -> User:
        user = session.get(User, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return user

    @staticmethod
    def _by_email(session: Session, email: str) -> User | None:
        return session.scalars(sa.select(User).where(User.email == _normalize_email(email))).first()

    @staticmethod
    def _by_identity(session: Session, provider: str, provider_uid: str) -> User | None:
        return session.scalars(
            sa.select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.provider == provider.lower(), UserIdentity.provider_uid == provider_uid)
        ).first()

    @staticmethod
    def _translate(
        exc: IntegrityError,
        *,
        email: str | None = None,
        player_name: str | None = None,
        provider: str | None = None,
        user_id: int | None = None,
    ) -> UserError | None:
        """The UserError matching a constraint error, or None if it is another one."""
        name = constraint_name(exc)
        if name == UNIQUE_EMAIL_CONSTRAINT and email is not None:
            return EmailTakenError(email)
        if name == UNIQUE_PLAYER_NAME_CONSTRAINT and player_name is not None:
            return PlayerNameTakenError(player_name)
        if name == UNIQUE_IDENTITY_CONSTRAINT and provider is not None:
            return IdentityTakenError(provider)
        if name in CONTENT_FK_CONSTRAINTS and user_id is not None:
            return UserHasContentError(user_id)
        return None
