"""Checks CourseService against the real database in .env (your Supabase).

    uv run python -m tests.test_course_service

Everything it creates (one test user, a few courses with codes starting
with TEST_) is deleted at the end, even if a check fails.
"""

from __future__ import annotations

import sys
import uuid

from dotenv import load_dotenv

import src.models  # noqa: F401
from src.course.dto.CourseDto import CourseCreate, CourseUpdate
from src.course.service.CourseErrors import (
    CourseCodeTakenError,
    CourseCreatorNotFoundError,
    CourseNotFoundError,
)
from src.course.service.CourseService import CourseService
from src.db.DB import Db

TEST_PREFIX = "TEST_"
MISSING_ID = 2_000_000_000  # an id no row will have

passed = 0
failed = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def expect_error(name: str, error_type: type[Exception], action) -> None:
    try:
        action()
    except error_type as exc:
        check(name, True)
        print(f"        -> {exc}")
    except Exception as exc:  # wrong error
        check(name, False, f"expected {error_type.__name__}, got {type(exc).__name__}: {exc}")
    else:
        check(name, False, f"expected {error_type.__name__}, nothing was raised")


def main() -> int:
    load_dotenv()
    db = Db.from_env()
    run = uuid.uuid4().hex[:6].upper()
    code_a = f"{TEST_PREFIX}{run}A"
    code_b = f"{TEST_PREFIX}{run}B"
    print(f"Database: {db}")

    user_id = db.scalar(
        "insert into users (name, email) values (:n, :e) returning id",
        {"n": "Course service test", "e": f"course-test-{run.lower()}@example.com"},
    )
    service = CourseService(db)

    try:
        print("\ncreate")
        course = service.create(
            CourseCreate(code=code_a, title="Networks", description="Intro to networks"),
            created_by=user_id,
        )
        check("returns an id", course.id > 0, course)
        check("fields saved", (course.code, course.title, course.description) == (code_a, "Networks", "Intro to networks"))
        check("created_by is the user", course.created_by == user_id)
        check("created_at set by the database", course.created_at is not None)

        print("\nread")
        check("get by id", service.get(course.id) == course)
        check("get by code", service.get_by_code(code_a) == course)
        check("get by unknown code is None", service.get_by_code(f"{TEST_PREFIX}NOPE") is None)
        service.create(CourseCreate(code=code_b, title="Algorithms"), created_by=user_id)
        listed = [c.code for c in service.list(limit=100) if c.code.startswith(TEST_PREFIX + run)]
        check("list contains both, ordered by code", listed == [code_a, code_b], listed)

        print("\nupdate")
        updated = service.update(course.id, CourseUpdate(title="Computer Networks"))
        check("title changed", updated.title == "Computer Networks")
        check("other fields kept", (updated.code, updated.description) == (code_a, "Intro to networks"))
        check("empty update keeps description", service.update(course.id, CourseUpdate()).description == "Intro to networks")
        check("description=None clears it", service.update(course.id, CourseUpdate(description=None)).description is None)

        print("\nerrors")
        expect_error("duplicate code on create", CourseCodeTakenError,
                     lambda: service.create(CourseCreate(code=code_a, title="x"), created_by=user_id))
        expect_error("duplicate code on update", CourseCodeTakenError,
                     lambda: service.update(course.id, CourseUpdate(code=code_b)))
        expect_error("unknown creator", CourseCreatorNotFoundError,
                     lambda: service.create(CourseCreate(code=f"{TEST_PREFIX}{run}C", title="x"), created_by=MISSING_ID))
        expect_error("get missing course", CourseNotFoundError, lambda: service.get(MISSING_ID))
        expect_error("update missing course", CourseNotFoundError,
                     lambda: service.update(MISSING_ID, CourseUpdate(title="x")))

        print("\ndelete")
        service.delete(course.id)
        expect_error("deleted course is gone", CourseNotFoundError, lambda: service.get(course.id))
        expect_error("delete twice", CourseNotFoundError, lambda: service.delete(course.id))
    finally:
        db.execute("delete from courses where code like :p", {"p": f"{TEST_PREFIX}{run}%"})
        db.execute("delete from users where id = :id", {"id": user_id})
        db.close()
        print("\ntest data removed")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
