"""Errors raised by UserService. Routes turn them into HTTP responses."""

from __future__ import annotations

from src.base.ServiceErrors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
    PermissionDeniedError,
    ServiceError,
)


class UserError(ServiceError):
    """Base class: catch this to handle every user error at once."""


class UserNotFoundError(UserError, NotFoundError):
    def __init__(self, user_id: int) -> None:
        super().__init__(f"User {user_id} does not exist")
        self.user_id = user_id


class EmailTakenError(UserError, ConflictError):
    def __init__(self, email: str) -> None:
        super().__init__(f"An account with email {email!r} already exists")
        self.email = email


class PlayerNameTakenError(UserError, ConflictError):
    def __init__(self, player_name: str) -> None:
        super().__init__(f"Player name {player_name!r} is already taken")
        self.player_name = player_name


class InvalidCredentialsError(UserError, PermissionDeniedError):
    def __init__(self) -> None:
        # same message for "no such email" and "wrong password", on purpose
        super().__init__("Wrong email or password")


class IdentityTakenError(UserError, ConflictError):
    def __init__(self, provider: str) -> None:
        super().__init__(f"This {provider} account is already linked to another user")
        self.provider = provider


class IdentityNotFoundError(UserError, NotFoundError):
    def __init__(self, identity_id: int) -> None:
        super().__init__(f"Identity {identity_id} does not exist for this user")
        self.identity_id = identity_id


class LastLoginMethodError(UserError, InvalidOperationError):
    def __init__(self) -> None:
        super().__init__("Cannot remove the last way this user can sign in")


class UserHasContentError(UserError, ConflictError):
    def __init__(self, user_id: int) -> None:
        super().__init__(
            f"User {user_id} authored courses, questions or exams; reassign or delete them first"
        )
        self.user_id = user_id


class EmailNotVerifiedError(UserError, PermissionDeniedError):
    """Right password, but the email was never verified: no log-in yet."""

    def __init__(self, user_id: int, email: str) -> None:
        super().__init__("Verify your email address before logging in")
        self.user_id = user_id
        self.email = email


class VerificationLinkError(UserError, InvalidOperationError):
    def __init__(self) -> None:
        super().__init__("This verification link is not valid any more")
