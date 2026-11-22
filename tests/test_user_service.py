"""Checks UserService against the real database in .env.

    uv run python -m tests.test_user_service

Every user it creates has an email ending in @studiamo-test.example and is
deleted at the end, even if a check fails.
"""

from __future__ import annotations

import sys
import uuid

import sqlalchemy as sa
from dotenv import load_dotenv
from pydantic import ValidationError

import src.models  # noqa: F401
from src.db.DB import Db
from src.user.dto.UserDto import UserCreate, UserRolesUpdate, UserUpdate
from src.user.dto.UserIdentityDto import UserIdentityCreate
from src.user.models.User import UserRole
from src.user.service.UserErrors import (
    EmailNotVerifiedError,
    EmailTakenError,
    IdentityTakenError,
    InvalidCredentialsError,
    LastLoginMethodError,
    PlayerNameTakenError,
    UserHasContentError,
    UserNotFoundError,
    VerificationLinkError,
)
from src.user.service.UserService import UserService
from tests.harness import MISSING_ID, Checks

DOMAIN = "studiamo-test.example"


def main() -> int:
    load_dotenv()
    db = Db.from_env()
    run = uuid.uuid4().hex[:6]
    t = Checks()
    service = UserService(db)
    email = f"Shin.{run}@{DOMAIN}"
    print(f"Database: {db}")

    try:
        t.section("create")
        user = service.create(UserCreate(name="Shin", email=email, password="secret123", player_name=f"shin{run}"))
        t.check("returns an id", user.id > 0)
        t.check("email lower-cased", user.email == email.lower(), user.email)
        t.check("new users are students", user.roles == [UserRole.STUDENT], user.roles)
        t.check("password is hashed", db.scalar("select password_hash from users where id=:i", {"i": user.id}).startswith("scrypt:"))
        t.check("player_stats row created by trigger", db.scalar("select total_exp from player_stats where user_id=:i", {"i": user.id}) == 0)

        t.section("email verification")
        t.check("password sign-up starts unverified", not user.email_verified)
        t.expect_error("no log-in before verifying", EmailNotVerifiedError, lambda: service.authenticate(email, "secret123"))
        t.expect_error("link for another email refused", VerificationLinkError,
                       lambda: service.mark_email_verified(user.id, f"other{run}@{DOMAIN}"))
        t.check("resend allowed the first time", service.claim_verification_send(email) is not None)
        t.check("resend refused within a minute", service.claim_verification_send(email) is None)
        user = service.mark_email_verified(user.id, email.upper())
        t.check("verified", user.email_verified)
        t.check("no resend once verified", service.claim_verification_send(email) is None)

        t.expect_error("duplicate email (any case)", EmailTakenError,
                       lambda: service.create(UserCreate(name="x", email=email.upper())))
        t.expect_error("duplicate player name", PlayerNameTakenError,
                       lambda: service.create(UserCreate(name="x", email=f"o{run}@{DOMAIN}", player_name=f"shin{run}")))

        t.section("read")
        t.check("get", service.get(user.id) == user)
        t.check("get_by_email ignores case", service.get_by_email(email.upper()) == user)
        t.check("get_public hides email", "email" not in service.get_public(user.id).model_dump())
        t.expect_error("get missing", UserNotFoundError, lambda: service.get(MISSING_ID))

        t.section("authenticate")
        t.check("right password", service.authenticate(email, "secret123").id == user.id)
        t.expect_error("wrong password", InvalidCredentialsError, lambda: service.authenticate(email, "nope-nope"))
        t.expect_error("unknown email", InvalidCredentialsError, lambda: service.authenticate(f"x{run}@{DOMAIN}", "secret123"))
        service.change_password(user.id, "secret123", "newsecret1")
        t.check("change_password", service.authenticate(email, "newsecret1").id == user.id)
        t.expect_error("change_password with wrong current", InvalidCredentialsError,
                       lambda: service.change_password(user.id, "bad-bad-bad", "x" * 10))

        t.section("update")
        updated = service.update(user.id, UserUpdate(name="Shinji"))
        t.check("name changed, rest kept", (updated.name, updated.player_name) == ("Shinji", f"shin{run}"))
        t.check("updated_at moved (trigger)", updated.updated_at >= user.updated_at)
        t.check("player_name=None clears it", service.update(user.id, UserUpdate(player_name=None)).player_name is None)
        roles = service.set_roles(user.id, UserRolesUpdate(roles=[UserRole.STUDENT, UserRole.LECTURER, UserRole.STUDENT]))
        t.check("set_roles (deduped)", roles.roles == [UserRole.STUDENT, UserRole.LECTURER])
        t.expect_error("'admin' is not a role in code", ValidationError, lambda: UserRolesUpdate(roles=["admin"]))
        t.expect_error("'admin' is not a role in the database", sa.exc.DataError,
                       lambda: db.execute("update users set roles = '{admin}' where id = :id", {"id": user.id}))
        t.expect_error("roles cannot be emptied in the database", sa.exc.IntegrityError,
                       lambda: db.execute("update users set roles = '{}' where id = :id", {"id": user.id}))
        t.check("has_role", service.has_role(user.id, UserRole.LECTURER))
        t.expect_error("roles cannot be empty", ValidationError, lambda: UserRolesUpdate(roles=[]))

        t.section("identities")
        ident = service.link_identity(user.id, UserIdentityCreate(provider="GitHub", provider_uid=f"gh-{run}"))
        t.check("link_identity lower-cases provider", ident.provider == "github")
        t.check("find_by_identity", service.find_by_identity("github", f"gh-{run}").id == user.id)
        t.expect_error("identity linked twice", IdentityTakenError,
                       lambda: service.link_identity(user.id, UserIdentityCreate(provider="github", provider_uid=f"gh-{run}")))
        same = service.sign_in_with_provider("github", f"gh-{run}", name="?", email="ignored@x.com", email_verified=False)
        t.check("provider sign-in finds linked user", same.id == user.id)
        linked = service.sign_in_with_provider("google", f"g-{run}", name="?", email=email, email_verified=True)
        t.check("verified email links existing account", linked.id == user.id and len(service.list_identities(user.id)) == 2)
        t.expect_error("unverified email does not link", EmailTakenError,
                       lambda: service.sign_in_with_provider("gitlab", f"l-{run}", name="?", email=email, email_verified=False))
        fresh = service.sign_in_with_provider("google", f"g2-{run}", name="Asuka", email=f"asuka.{run}@{DOMAIN}", email_verified=True)
        t.check("unknown provider user signs up without password", fresh.id != user.id and fresh.name == "Asuka")
        only = service.list_identities(fresh.id)[0]
        t.expect_error("cannot remove last login method", LastLoginMethodError,
                       lambda: service.unlink_identity(fresh.id, only.id))
        service.unlink_identity(user.id, ident.id)
        t.check("unlink when a password exists", len(service.list_identities(user.id)) == 1)

        t.section("delete")
        course_id = db.scalar("insert into courses (code, title, created_by) values (:c, 'x', :u) returning id",
                              {"c": f"TESTU_{run}", "u": user.id})
        t.expect_error("author of a course cannot be deleted", UserHasContentError, lambda: service.delete(user.id))
        db.execute("delete from courses where id=:i", {"i": course_id})
        service.delete(user.id)
        t.expect_error("deleted user is gone", UserNotFoundError, lambda: service.get(user.id))
    finally:
        db.execute("delete from courses where code like :p", {"p": f"TESTU_{run}%"})
        db.execute("delete from users where email like :d", {"d": f"%{run}@{DOMAIN}"})
        db.close()
        print("\ntest data removed")

    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
