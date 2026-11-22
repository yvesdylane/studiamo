"""Errors raised by PlayerService."""

from __future__ import annotations

from src.base.ServiceErrors import NotFoundError, ServiceError


class PlayerError(ServiceError):
    """Base class: catch this to handle every player error at once."""


class PlayerNotFoundError(PlayerError, NotFoundError):
    def __init__(self, user_id: int) -> None:
        super().__init__(f"User {user_id} has no player stats (the user does not exist)")
        self.user_id = user_id
