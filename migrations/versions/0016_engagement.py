"""daily tasks, streaks, badges, notifications (and the skill graph's
Streak / Time / Badges axes)

  student_daily_tasks   3 tasks per student per day (src/progress/service/Engagement.py);
                        each gives exp + Xexp when done
  student_study_days    days with at least one daily task done -> the streak
  student_badges        badges earned (the list of badges lives in Engagement.py)
  user_notifications    messages shown as pop-ups on the user's next page
  users.toast_enabled / users.toast_seconds   how pop-ups are shown
  exp_ledger.source     may now also be 'daily_task' or 'badge'

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        create table student_daily_tasks (
            id            serial primary key,
            student_id    integer not null references users(id) on delete cascade,
            day           date not null,
            task_key      varchar(30) not null,
            target        integer not null check (target > 0),
            progress      integer not null default 0,
            completed_at  timestamptz,
            unique (student_id, day, task_key)
        );
        create table student_study_days (
            student_id  integer references users(id) on delete cascade,
            day         date not null,
            primary key (student_id, day)
        );
        create table student_badges (
            student_id  integer references users(id) on delete cascade,
            badge_key   varchar(30) not null,
            earned_at   timestamptz not null default now(),
            primary key (student_id, badge_key)
        );
        create table user_notifications (
            id          bigserial primary key,
            user_id     integer not null references users(id) on delete cascade,
            text        varchar(300) not null,
            kind        varchar(20) not null default 'info',
            created_at  timestamptz not null default now(),
            seen_at     timestamptz
        );
        create index user_notifications_unseen_idx on user_notifications (user_id) where seen_at is null;

        alter table users
            add column toast_enabled boolean not null default true,
            add column toast_seconds integer not null default 10 check (toast_seconds between 3 and 60);

        alter table exp_ledger drop constraint if exists exp_ledger_source_check;
        alter table exp_ledger add constraint exp_ledger_source_check
            check (source in ('note', 'exercise', 'exam', 'daily_task', 'badge'));
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        delete from exp_ledger where source in ('daily_task', 'badge');
        alter table exp_ledger drop constraint if exists exp_ledger_source_check;
        alter table exp_ledger add constraint exp_ledger_source_check check (source in ('note', 'exercise', 'exam'));
        alter table users drop column toast_seconds, drop column toast_enabled;
        drop table user_notifications, student_badges, student_study_days, student_daily_tasks;
        """
    )
