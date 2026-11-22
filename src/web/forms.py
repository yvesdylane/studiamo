"""Turning pydantic validation errors into messages next to form fields."""

from __future__ import annotations

from pydantic import ValidationError

MESSAGES = {
    "string_too_short": "This field is required.",
    "value_error": "Enter a valid email address.",
    "missing": "This field is required.",
}


def field_errors(exc: ValidationError, rename: dict[str, str] | None = None) -> dict[str, str]:
    """{form field name: first message}. rename maps DTO field -> form field."""
    rename = rename or {}
    errors: dict[str, str] = {}
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else "form"
        field = rename.get(field, field)
        if field in errors:
            continue
        if err["type"] == "string_too_short" and field == "password":
            errors[field] = "Use at least 8 characters."
        elif err["type"] == "string_too_long":
            errors[field] = f"Too long: at most {err['ctx']['max_length']} characters."
        else:
            errors[field] = MESSAGES.get(err["type"], err["msg"])
    return errors
