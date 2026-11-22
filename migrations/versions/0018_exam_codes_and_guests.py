"""exams joined with a code, without a course, and by guests

  exams.course_id         may now be null: an exam a lecturer makes for their own
                          students, not attached to any course (scheduled only)
  exams.join_code         short code students type in "Join an exam" (unique)
  exams.join_enabled      the code works only while this is true
  exams.join_mode         'anyone' (anyone with the code) | 'list' (only emails on the class list)
  exams.guest_results     'graded' (emailed as soon as an attempt is graded) | 'manual'
                          (emailed when the lecturer presses "Email results to guests")
  exam_allowed_emails     the class list, for join_mode = 'list'
  exam_guests             people who joined with name + email, no account; their email
                          is checked with a 6-digit code (stored hashed)
  exam_attempts           belong to a student OR a guest (exactly one)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table exams alter column course_id drop not null;
        alter table exams
            add column join_code varchar(12) unique,
            add column join_enabled boolean not null default false,
            add column join_mode varchar(10) not null default 'anyone' check (join_mode in ('anyone', 'list')),
            add column guest_results varchar(10) not null default 'graded' check (guest_results in ('graded', 'manual')),
            add constraint exams_course_or_scheduled check (course_id is not null or kind = 'scheduled');

        create table exam_allowed_emails (
            exam_id  integer references exams(id) on delete cascade,
            email    varchar(255) not null,
            primary key (exam_id, email)
        );

        create table exam_guests (
            id               serial primary key,
            exam_id          integer not null references exams(id) on delete cascade,
            email            varchar(255) not null,
            name             varchar(100) not null,
            code_hash        char(64),
            code_expires_at  timestamptz,
            code_tries       integer not null default 0,
            code_sent_at     timestamptz,
            verified_at      timestamptz,
            results_sent_at  timestamptz,
            created_at       timestamptz not null default now(),
            unique (exam_id, email)
        );

        alter table exam_attempts alter column student_id drop not null;
        alter table exam_attempts
            add column guest_id integer references exam_guests(id) on delete cascade,
            add constraint exam_attempts_one_owner check ((student_id is null) <> (guest_id is null)),
            add constraint exam_attempts_guest_no_key unique (guest_id, exam_id, attempt_no);

        create or replace view student_courses as
        select scn.student_id, ct.course_id
        from student_completed_notes scn
        join topic_notes tn   on tn.id = scn.note_id
        join course_topics ct on ct.id = tn.topic_id
        union
        select ea.student_id, ct.course_id
        from exercise_attempts ea
        join exercises e      on e.id = ea.exercise_id
        join course_topics ct on ct.id = e.topic_id
        union
        select xa.student_id, x.course_id
        from exam_attempts xa
        join exams x on x.id = xa.exam_id
        where xa.student_id is not null and x.course_id is not null;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("0018 lets attempts belong to guests; restore from a backup instead")
