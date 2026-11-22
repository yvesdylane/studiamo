"""Errors raised by LecturerApplicationService."""

from __future__ import annotations

from src.base.ServiceErrors import ConflictError, InvalidOperationError, NotFoundError
from src.user.service.UserErrors import UserError


class LecturerApplicationNotFoundError(UserError, NotFoundError):
    def __init__(self, application_id: int) -> None:
        super().__init__(f"Lecturer application {application_id} does not exist")
        self.application_id = application_id


class LecturerApplicationExistsError(UserError, ConflictError):
    def __init__(self, user_id: int) -> None:
        super().__init__(f"User {user_id} already applied to be a lecturer")
        self.user_id = user_id


class LecturerApplicationReviewedError(UserError, InvalidOperationError):
    def __init__(self, application_id: int, status: str) -> None:
        super().__init__(f"Lecturer application {application_id} was already {status}")
        self.application_id = application_id
        self.status = status
