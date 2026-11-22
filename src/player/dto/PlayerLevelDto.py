"""Read-only DTO for the `player_levels` view (level, rank progress and the
"to next" amounts are computed in SQL, migration 0006)."""

from __future__ import annotations

from src.base.BaseDto import DTO
from src.player.PlayerStats import PlayerRank


class PlayerLevelRead(DTO):  # row of the player_levels view
    user_id: int
    total_exp: int
    total_xexp: float
    rank: PlayerRank
    rank_level: int
    level: int
    exp_in_level: int
    exp_to_next_level: int            # exp this level costs in total
    xexp_in_rank: float
    xexp_to_next_rank: float | None   # None at S5, the top

    @property
    def rank_label(self) -> str:
        from src.progress.service.ExpRules import rank_label

        return rank_label(self.rank_level)
