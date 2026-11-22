"""Tiny pass/fail helpers shared by the service tests (same style as
test_course_service.py: run against the real database in .env)."""

from __future__ import annotations

from collections.abc import Callable

MISSING_ID = 2_000_000_000  # an id no row will have


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        if ok:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}  {detail}")

    def expect_error(self, name: str, error_type: type[Exception], action: Callable[[], object]) -> None:
        try:
            action()
        except error_type as exc:
            self.check(name, True)
            print(f"        -> {exc}")
        except Exception as exc:  # wrong error
            self.check(name, False, f"expected {error_type.__name__}, got {type(exc).__name__}: {exc}")
        else:
            self.check(name, False, f"expected {error_type.__name__}, nothing was raised")

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def summary(self) -> int:
        print(f"\n{self.passed} passed, {self.failed} failed")
        return 1 if self.failed else 0
