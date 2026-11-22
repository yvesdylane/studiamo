"""Xexp, new level curve, ranks with 5 levels per letter

Levels (exp):  going from level L to L+1 costs 1.5^L * 10 + 10 exp, from level 0.
               Total to reach L = 20 * (1.5^L - 1) + 10 * L, rounded UP to whole exp.
Ranks (Xexp):  rank level r -> r+1 costs 1.5^r * 5 + 5 Xexp. Total to reach r =
               10 * (1.5^r - 1) + 5 * r. 35 rank levels: F1..F5, E1..E5, ... S1..S5
               (letter = r / 5, sub-level = r % 5 + 1). player_stats.rank keeps the letter.
Xexp:          exercises and exams: max_exp * 0.85 * best_score% when best_score >= 75,
               else 0; only the best attempt counts. Notes: 2.5% of max_exp, once.
               The same rules live in src/progress/service/ExpRules.py.

performance_score (the old rank driver) is dropped. Existing marks get their
Xexp computed from these rules.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        -- ---------------------------------------------------------- columns
        alter table player_stats
            add column total_xexp numeric(14,2) not null default 0 check (total_xexp >= 0),
            add column rank_level integer not null default 0 check (rank_level between 0 and 34),
            drop column performance_score;
        alter table student_mark_for_exercise add column xexp_earned numeric(12,2) not null default 0;
        alter table student_mark_for_exams    add column xexp_earned numeric(12,2) not null default 0;
        alter table student_completed_notes   add column xexp_awarded numeric(12,2) not null default 0;
        alter table exp_ledger                add column xexp numeric(12,2) not null default 0;
        create index player_stats_total_xexp_idx on player_stats (total_xexp desc);

        -- ---------------------------------------------------------- curves
        drop view if exists player_levels;
        drop function if exists level_from_exp(bigint);

        create function level_exp_total(lvl integer) returns bigint
        language sql immutable as $$
            select ceil(20 * (power(1.5::numeric, lvl) - 1) + 10 * lvl)::bigint
        $$;

        create function level_from_exp(total bigint) returns integer
        language plpgsql immutable as $$
        declare
            lvl integer := 0;
        begin
            while lvl < 200 and level_exp_total(lvl + 1) <= total loop
                lvl := lvl + 1;
            end loop;
            return lvl;
        end
        $$;

        create function rank_xexp_total(r integer) returns numeric
        language sql immutable as $$
            select 10 * (power(1.5::numeric, r) - 1) + 5 * r
        $$;

        create function rank_level_from_xexp(total numeric) returns integer
        language plpgsql immutable as $$
        declare
            r integer := 0;
        begin
            while r < 34 and rank_xexp_total(r + 1) <= total loop
                r := r + 1;
            end loop;
            return r;
        end
        $$;

        create function rank_letter(r integer) returns player_rank
        language sql immutable as $$
            select (array['F','E','D','C','B','A','S'])[r / 5 + 1]::player_rank
        $$;

        create view player_levels as
        select p.user_id,
               p.total_exp,
               p.total_xexp,
               p.rank,
               p.rank_level,
               l.level,
               p.total_exp - level_exp_total(l.level)                        as exp_in_level,
               level_exp_total(l.level + 1) - level_exp_total(l.level)       as exp_to_next_level,
               round(p.total_xexp - rank_xexp_total(p.rank_level), 2)        as xexp_in_rank,
               case when p.rank_level >= 34 then null
                    else round(rank_xexp_total(p.rank_level + 1) - rank_xexp_total(p.rank_level), 2)
               end                                                           as xexp_to_next_rank
        from player_stats p
        cross join lateral (select level_from_exp(p.total_exp) as level) l;

        -- ------------------------------------------------- existing data
        update student_mark_for_exercise m
           set xexp_earned = case when m.best_score_pct >= 75
                                  then trunc(e.max_exp * 0.85 * m.best_score_pct / 100, 2) else 0 end
          from exercises e where e.id = m.exercise_id;
        update student_mark_for_exams m
           set xexp_earned = case when m.best_score_pct >= 75
                                  then trunc(x.max_exp * 0.85 * m.best_score_pct / 100, 2) else 0 end
          from exams x where x.id = m.exam_id;
        update student_completed_notes c
           set xexp_awarded = trunc(n.max_exp * 0.025, 2)
          from topic_notes n where n.id = c.note_id;

        update player_stats p
           set total_xexp = coalesce((select sum(xexp_earned) from student_mark_for_exercise where student_id = p.user_id), 0)
                          + coalesce((select sum(xexp_earned) from student_mark_for_exams    where student_id = p.user_id), 0)
                          + coalesce((select sum(xexp_awarded) from student_completed_notes  where student_id = p.user_id), 0);
        update player_stats
           set rank_level = rank_level_from_xexp(total_xexp),
               rank = rank_letter(rank_level_from_xexp(total_xexp));
        """
    )


def downgrade() -> None:
    raise NotImplementedError("0006 changes how progress is computed; restore from a backup instead")
