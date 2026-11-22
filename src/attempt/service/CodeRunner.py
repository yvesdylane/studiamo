"""How code answers get executed. The project does not ship a sandbox yet:
plug one in (Judge0, Piston, a Docker runner...) by implementing CodeRunner
and passing it to AttemptService. Without one, code answers wait for a
lecturer to grade them by hand, like short answers.

Never run student code in the web process itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.question.models.Question import CodeLanguage


@dataclass(frozen=True)
class RunResult:
    stdout: str
    error: str | None = None      # compile error, runtime error, timeout... None if it ran fine


class CodeRunner(Protocol):
    def run(self, language: CodeLanguage, source: str, stdin: str) -> RunResult:
        """Run source with stdin and return what it printed."""
        ...


def same_output(actual: str, expected: str) -> bool:
    """Outputs match ignoring line endings, trailing spaces and trailing blank lines."""
    def norm(text: str) -> list[str]:
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        return lines

    return norm(actual) == norm(expected)
