"""ranks grow by 1.18 per level instead of 1.5

rank level r -> r+1 now costs 1.18^r * 5 + 5 Xexp. Total to reach r =
5 * (1.18^r - 1) / 0.18 + 5 * r. Every player's rank is recomputed.
Same rule: RANK_GROWTH in src/progress/service/ExpRules.py.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

FUNC = """
create or replace function rank_xexp_total(r integer) returns numeric
language sql immutable as $$
    select 5 * (power({g}::numeric, r) - 1) / ({g}::numeric - 1) + 5 * r
$$;
update player_stats
   set rank_level = rank_level_from_xexp(total_xexp),
       rank = rank_letter(rank_level_from_xexp(total_xexp));
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(FUNC.format(g="1.18"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(FUNC.format(g="1.5"))
