"""users can only be students or lecturers: drop 'admin' from user_role

Admins are no longer stored in the database at all. Who is an admin is decided
at login, from ADMIN_EMAILS in .env and a Google-verified email (see
src/web/security.py), so write access to the database cannot create one.

Postgres cannot remove a value from an enum, so the type is rebuilt.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        update users set roles = array_remove(roles, 'admin'::user_role) where 'admin' = any(roles);
        update users set roles = '{student}' where cardinality(roles) = 0;

        alter table users alter column roles drop default;
        create type user_role_v2 as enum ('student', 'lecturer');
        alter table users alter column roles type user_role_v2[] using roles::text[]::user_role_v2[];
        drop type user_role;
        alter type user_role_v2 rename to user_role;
        alter table users alter column roles set default '{student}';

        alter table users add constraint users_roles_not_empty check (cardinality(roles) >= 1);
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table users drop constraint if exists users_roles_not_empty;
        alter type user_role add value if not exists 'admin';
        """
    )
