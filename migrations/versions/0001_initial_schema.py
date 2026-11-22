"""initial schema: tables, enums, functions, triggers and views

Runs migrations/sql/0001_initial_schema.sql (a frozen copy of studiamo.sql).
Later changes go in new revisions, never in this file or its .sql.

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0001_initial_schema.sql"


def upgrade() -> None:
    # exec_driver_sql: no :param parsing, so the $$ function bodies are safe
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        drop view if exists student_courses;
        drop view if exists player_levels;

        drop table if exists exp_ledger, student_mark_for_exams, student_mark_for_exercise,
            student_completed_notes, exam_attempt_questions, exam_attempts, exercise_attempts,
            exam_topics, exam_questions, exams, note_exercises, exercise_questions, exercises,
            question_test_cases, question_options, questions, topic_notes, course_topics,
            courses, player_stats, user_identities, users cascade;

        drop function if exists level_from_exp(bigint);
        drop function if exists create_player_stats();
        drop function if exists set_updated_at();

        drop type if exists attempt_status, exam_kind, code_language, question_type,
            player_rank, user_role;
        """
    )
