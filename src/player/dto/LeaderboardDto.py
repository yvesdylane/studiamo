"""One line of the leaderboard (computed from player_levels + users)."""

from __future__ import annotations

from src.base.BaseDto import DTO
from src.player.PlayerStats import PlayerRank
from src.user.dto.UserDto import UserPublic


class LeaderboardEntry(DTO):
    position: int          # 1 = most exp; ties share a position
    user: UserPublic | None   # None for players who chose to be anonymous
    anonymous: bool = False
    you: bool = False         # the viewer's own line
    total_exp: int
    level: int
    rank: PlayerRank       # letter
    rank_level: int = 0    # 0..34 = F1..S5
    total_xexp: float = 0.0

    @property
    def rank_label(self) -> str:
        from src.progress.service.ExpRules import rank_label

        return rank_label(self.rank_level)
