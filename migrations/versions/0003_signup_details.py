"""sign-up details from the registration pages

  users.class_name          the class a student picks when signing up (HND 1, HND 2, Other)
  users.terms_accepted_at   when the user accepted the terms (incl. being recorded in
                            supervised exams); null for accounts made before this
  lecturer_applications     a lecturer sign-up waiting for an admin to approve it; the
                            'lecturer' role is only added on approval

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table users
            add column if not exists class_name varchar(50),
            add column if not exists terms_accepted_at timestamptz;

        create type lecturer_application_status as enum ('pending', 'approved', 'rejected');

        create table lecturer_applications (
            id              serial primary key,
            user_id         integer not null unique references users(id) on delete cascade,
            institution     varchar(200) not null,
            courses_taught  text not null,
            status          lecturer_application_status not null default 'pending',
            reviewed_by     integer references users(id) on delete set null,
            reviewed_at     timestamptz,
            created_at      timestamptz not null default now()
        );

        create index lecturer_applications_status_idx on lecturer_applications (status, created_at);
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        drop table if exists lecturer_applications;
        drop type if exists lecturer_application_status;
        alter table users drop column if exists terms_accepted_at, drop column if exists class_name;
        """
    )
