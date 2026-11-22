"""Where the .md files live: note bodies (topic_notes.md_path) and attempt
records (exercise_attempts.md_path / exam_attempts.md_path).

The database stores only the key returned by save(). Swap LocalMarkdownStore
for a Supabase Storage / S3 implementation later without touching services:
anything with save / read / delete works.

    store = LocalMarkdownStore.from_env()      # MARKDOWN_DIR, default ./data/md
    key   = store.save("attempts/exercise/7/12.md", "# Attempt ...")
    text  = store.read(key)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class MarkdownStore(Protocol):
    def save(self, key: str, text: str) -> str:
        """Write text under key (overwriting), return the key to store in md_path."""
        ...

    def read(self, key: str) -> str:
        """Text saved under key. Raises FileNotFoundError when there is none."""
        ...

    def delete(self, key: str) -> None:
        """Remove key if it exists."""
        ...


class LocalMarkdownStore:
    """Files on local disk under one root folder."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @classmethod
    def from_env(cls) -> LocalMarkdownStore:
        return cls(os.getenv("MARKDOWN_DIR", "data/md"))

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):  # "../../etc/passwd"
            raise ValueError(f"Key {key!r} points outside the store")
        return path

    def save(self, key: str, text: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)  # atomic: readers never see half a file
        return key

    def read(self, key: str) -> str:
        return self._path(key).read_text(encoding="utf-8")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def __repr__(self) -> str:
        return f"LocalMarkdownStore({str(self.root)!r})"
