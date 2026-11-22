"""Sending email.

    mailer = Mailer.from_env()
    mailer.send(Email(to="a@b.com", subject="Hi", text="...", html="<p>...</p>"))

RESEND_API_KEY set  -> ResendMailer (https://resend.com), from MAIL_FROM
otherwise           -> LogMailer: prints the email to the log (local development)

With Resend's test sender (onboarding@resend.dev) mail is only delivered to
the address of your own Resend account; verify a domain in Resend to send to
anyone, then set MAIL_FROM="Studiamo <no-reply@your-domain>".
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

import requests

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Studiamo <onboarding@resend.dev>"


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str


class MailError(Exception):
    pass


class Mailer(Protocol):
    def send(self, email: Email) -> None: ...


class LogMailer:
    def send(self, email: Email) -> None:
        log.warning("email not sent (no RESEND_API_KEY); to=%s subject=%r\n%s", email.to, email.subject, email.text)


class ResendMailer:
    def __init__(self, api_key: str, sender: str, timeout: float = 10) -> None:
        self._api_key = api_key
        self._sender = sender
        self._timeout = timeout

    def send(self, email: Email) -> None:
        try:
            r = requests.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"from": self._sender, "to": [email.to], "subject": email.subject,
                      "text": email.text, "html": email.html},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise MailError(f"Resend unreachable: {exc}") from exc
        if r.status_code >= 300:
            raise MailError(f"Resend refused the email ({r.status_code}): {r.text[:300]}")


def from_env() -> Mailer:
    key = os.getenv("RESEND_API_KEY")
    if key:
        return ResendMailer(key, os.getenv("MAIL_FROM") or DEFAULT_FROM)
    return LogMailer()
