"""ORM model for table `exp_ledger`."""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base, NowTS


class ExpLedger(Base):
    __tablename__ = "exp_ledger"
    __table_args__ = (
        sa.CheckConstraint(
            "source in ('note', 'exercise', 'exam', 'daily_task', 'badge')", name="exp_ledger_source_check"
        ),
        sa.Index("exp_ledger_student_idx", "student_id", sa.text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(sa.String(20))  # 'note' | 'exercise' | 'exam' | 'daily_task' | 'badge'
    source_id: Mapped[int]
    amount: Mapped[int]                     # exp
    xexp: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), server_default="0")
    created_at: Mapped[NowTS]
