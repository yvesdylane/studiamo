"""Player side of a user: exp, level, rank and the leaderboard.

    service = PlayerService(get_db())
    service.get_stats(7)          # total_exp, total_xexp, rank_level (F1..S5), rank letter
    service.get_level(7)          # level and rank progress (player_levels view)
    service.leaderboard(limit=10)
    service.position_of(7)        # 1 = first

Exp is only ever added through ProgressService / AttemptService.
"""

from __future__ import annotations

from src.base.Paging import DEFAULT_PAGE_SIZE, clamp
from src.db.DB import Db
from src.player.dto.LeaderboardDto import LeaderboardEntry
from src.player.dto.PlayerLevelDto import PlayerLevelRead
from src.player.dto.PlayerStatsDto import PlayerStatsRead
from src.player.PlayerStats import PlayerStats
from src.player.service.PlayerErrors import PlayerNotFoundError
from src.user.dto.UserDto import UserPublic

# Players who chose 'hidden' are left out (and do not take up positions);
# 'anonymous' players are listed without their name or id.
# rank() is computed over every listed player before the page is cut.
_LEADERBOARD_SQL = """
select * from (
    select rank() over (order by pl.total_exp desc) as position,
           u.id, u.name, u.player_name, u.ranking_visibility,
           pl.total_exp, pl.level, pl.rank, pl.rank_level, pl.total_xexp
    from player_levels pl
    join users u on u.id = pl.user_id
    where u.ranking_visibility <> 'hidden'
) t
order by position, id
limit :limit offset :offset
"""
ANONYMOUS_NAME = "Anonymous player"


class PlayerService:
    def __init__(self, db: Db) -> None:
        self._db = db

    def get_stats(self, user_id: int) -> PlayerStatsRead:
        with self._db.session() as session:
            stats = session.get(PlayerStats, user_id)
            if stats is None:
                raise PlayerNotFoundError(user_id)
            return PlayerStatsRead.model_validate(stats)

    def get_level(self, user_id: int) -> PlayerLevelRead:
        row = self._db.fetch_one("select * from player_levels where user_id = :u", {"u": user_id})
        if row is None:
            raise PlayerNotFoundError(user_id)
        return PlayerLevelRead.model_validate(row)

    def leaderboard(
        self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0, viewer_id: int | None = None
    ) -> list[LeaderboardEntry]:
        """Players by total exp, most first, as each chose to appear (see
        users.ranking_visibility). viewer_id marks the viewer's own line."""
        limit, offset = clamp(limit, offset)
        rows = self._db.fetch_all(_LEADERBOARD_SQL, {"limit": limit, "offset": offset})
        return [
            LeaderboardEntry(
                position=r["position"],
                user=None if r["ranking_visibility"] == "anonymous"
                else UserPublic(id=r["id"], name=r["name"], player_name=r["player_name"]),
                anonymous=r["ranking_visibility"] == "anonymous",
                you=viewer_id is not None and r["id"] == viewer_id,
                total_exp=r["total_exp"],
                level=r["level"],
                rank=r["rank"],
                rank_level=r["rank_level"],
                total_xexp=float(r["total_xexp"]),
            )
            for r in rows
        ]

    def position_of(self, user_id: int) -> int:
        """1-based leaderboard position among listed players (ties share it).
        For a player who hides from the ranking: where they would be."""
        position = self._db.scalar(
            """
            select 1 + count(*) from player_stats p join users u on u.id = p.user_id
            where u.ranking_visibility <> 'hidden'
              and p.user_id <> :u
              and p.total_exp > (select total_exp from player_stats where user_id = :u)
            """,
            {"u": user_id},
        )
        if self._db.scalar("select 1 from player_stats where user_id = :u", {"u": user_id}) is None:
            raise PlayerNotFoundError(user_id)
        return int(position)
