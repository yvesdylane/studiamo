"""CRUD for topic notes (the lessons a student reads), and their markdown body.

    service = NoteService(get_db(), LocalMarkdownStore.from_env())
    note    = service.create(NoteCreate(topic_id=3, title="Sockets", md_path="notes/3/sockets.md",
                                        position=0, max_exp=20))
    service.save_content(note.id, "# Sockets\n...")      # writes the file at md_path
    text    = service.read_content(note.id)
    notes   = service.list_for_topic(3, published_only=True)     # student view

Completing a note and earning its exp is ProgressService.complete_note.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.course.dto.NoteDto import NoteCreate, NoteRead, NoteUpdate
from src.course.modles.Note import Note
from src.course.modles.Topic import Topic
from src.course.service.TopicErrors import (
    NoteNotFoundError,
    NoteNotPublishedError,
    NoteOrderMismatchError,
    TopicNotFoundError,
)
from src.db.DB import Db
from src.storage.MarkdownStore import MarkdownStore


class NoteService:
    def __init__(self, db: Db, store: MarkdownStore | None = None) -> None:
        """store is only needed for read_content / save_content."""
        self._db = db
        self._store = store

    # --------------------------------------------------------------- crud
    def create(self, data: NoteCreate) -> NoteRead:
        with self._db.session() as session:
            if session.get(Topic, data.topic_id) is None:
                raise TopicNotFoundError(data.topic_id)
            note = Note(**data.model_dump())
            session.add(note)
            session.flush()
            session.refresh(note)
            return NoteRead.model_validate(note)

    def get(self, note_id: int) -> NoteRead:
        with self._db.session() as session:
            return NoteRead.model_validate(self._find(session, note_id))

    def get_published(self, note_id: int) -> NoteRead:
        """Student view: NoteNotPublishedError for drafts."""
        note = self.get(note_id)
        if not note.published:
            raise NoteNotPublishedError(note_id)
        return note

    def list_for_topic(self, topic_id: int, *, published_only: bool = False) -> list[NoteRead]:
        with self._db.session() as session:
            if session.get(Topic, topic_id) is None:
                raise TopicNotFoundError(topic_id)
            query = sa.select(Note).where(Note.topic_id == topic_id)
            if published_only:
                query = query.where(Note.published)
            notes = session.scalars(query.order_by(Note.position, Note.id)).all()
            return [NoteRead.model_validate(n) for n in notes]

    def list_for_course(self, course_id: int, *, published_only: bool = False) -> list[NoteRead]:
        """Every note of a course, in topic order then note order."""
        with self._db.session() as session:
            query = (
                sa.select(Note)
                .join(Topic, Topic.id == Note.topic_id)
                .where(Topic.course_id == course_id)
            )
            if published_only:
                query = query.where(Note.published)
            notes = session.scalars(query.order_by(Topic.position, Note.position, Note.id)).all()
            return [NoteRead.model_validate(n) for n in notes]

    def update(self, note_id: int, data: NoteUpdate) -> NoteRead:
        """Partial update: None means "leave it" (no note field can be cleared)."""
        with self._db.session() as session:
            note = self._find(session, note_id)
            for field, value in data.model_dump(exclude_none=True).items():
                setattr(note, field, value)
            session.flush()
            return NoteRead.model_validate(note)

    def move(self, note_id: int, topic_id: int, position: int) -> NoteRead:
        """Move a note to another topic (or another place in the same one)."""
        with self._db.session() as session:
            note = self._find(session, note_id)
            if session.get(Topic, topic_id) is None:
                raise TopicNotFoundError(topic_id)
            note.topic_id = topic_id
            note.position = max(0, position)
            session.flush()
            return NoteRead.model_validate(note)

    def reorder(self, topic_id: int, note_ids: list[int]) -> list[NoteRead]:
        """Give the topic's notes positions 0..n-1 in the order of note_ids."""
        with self._db.session() as session:
            if session.get(Topic, topic_id) is None:
                raise TopicNotFoundError(topic_id)
            notes = {n.id: n for n in session.scalars(sa.select(Note).where(Note.topic_id == topic_id))}
            if len(note_ids) != len(set(note_ids)) or set(note_ids) != set(notes):
                raise NoteOrderMismatchError(topic_id)
            for i, nid in enumerate(note_ids):
                notes[nid].position = i
            session.flush()
            return [NoteRead.model_validate(notes[nid]) for nid in note_ids]

    def delete(self, note_id: int, *, delete_content: bool = False) -> None:
        """Delete a note (and the students' completions of it). The md file is
        kept unless delete_content=True."""
        with self._db.session() as session:
            note = self._find(session, note_id)
            md_path = note.md_path
            session.delete(note)
        if delete_content:
            self._require_store().delete(md_path)

    # ------------------------------------------------------------ content
    def read_content(self, note_id: int) -> str:
        """The note's markdown. FileNotFoundError if the file was never saved."""
        return self._require_store().read(self.get(note_id).md_path)

    def save_content(self, note_id: int, markdown: str) -> NoteRead:
        """Write the note's text, and its estimated reading time (skill graph)."""
        from src.content.StudiamoMarkdown import reading_seconds

        note = self.get(note_id)
        self._require_store().save(note.md_path, markdown)
        with self._db.session() as session:
            row = self._find(session, note_id)
            row.reading_seconds = reading_seconds(markdown)
            session.flush()
            return NoteRead.model_validate(row)

    # ------------------------------------------------------------ helpers
    def _require_store(self) -> MarkdownStore:
        if self._store is None:
            raise RuntimeError("NoteService was created without a MarkdownStore")
        return self._store

    @staticmethod
    def _find(session: Session, note_id: int) -> Note:
        note = session.get(Note, note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note
