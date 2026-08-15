from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base, TimestampMixin


class ReportStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    # The parcel cannot be processed (foral cadastre, no geometry). The frontend promises
    # a full refund in that case, so it is a distinct state a human has to act on.
    REFUND_DUE = "refund_due"


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Public, unguessable handle: the delivery link carries no account and no login.
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    refcat: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str] = mapped_column(String(255))
    parcel_id: Mapped[int | None] = mapped_column(ForeignKey("parcels.id"), default=None)

    status: Mapped[str] = mapped_column(String(20), default=ReportStatus.PENDING, index=True)
    # Everything the pipeline collected: findings, layer hits, NDVI, images metadata.
    # Kept so a report can be re-rendered without hitting the public services again.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    pdf_path: Mapped[str | None] = mapped_column(String(512), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    delivered_at: Mapped[datetime | None] = mapped_column(default=None)
    generated_at: Mapped[datetime | None] = mapped_column(default=None)
