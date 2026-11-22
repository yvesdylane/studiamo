"""DTOs for PlayerStats. ORM model: src/player/PlayerStats.py."""

from __future__ import annotations

from datetime import datetime

from src.base.BaseDto import DTO
from src.player.PlayerStats import PlayerRank


class PlayerStatsRead(DTO):
    user_id: int
    total_exp: int            # drives the level
    total_xexp: float         # drives the rank
    rank: PlayerRank          # the letter of rank_level
    rank_level: int           # 0..34 = F1..S5
    rank_updated_at: datetime | None = None

    @property
    def rank_label(self) -> str:
        from src.progress.service.ExpRules import rank_label

        return rank_label(self.rank_level)
