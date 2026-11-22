"""DTOs for ExpLedger. ORM model: src/progress/ExpLedger.py."""

from __future__ import annotations

from datetime import datetime

from src.base.BaseDto import DTO


class ExpLedgerRead(DTO):
    id: int
    student_id: int
    source: str
    source_id: int
    amount: int        # exp
    xexp: float = 0.0
    created_at: datetime
