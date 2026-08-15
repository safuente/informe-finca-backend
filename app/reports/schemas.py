from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr

from app.parcels.schemas import REFCAT_PATTERN, RefcatMixin
from app.reports.models import ReportStatus


class Severity(StrEnum):
    """Wording rules from the SPEC: every finding carries one of these, never a score."""

    INCIDENCIA = "INCIDENCIA"
    OBSERVACION = "OBSERVACIÓN"
    AFECCION = "AFECCIÓN"
    CONFORME = "CONFORME"


class Confidence(StrEnum):
    ALTA = "ALTA"  # official datum, read directly from the source
    MEDIA = "MEDIA"  # inference from data — always worded as "compatible con"


class Finding(BaseModel):
    severity: Severity
    title: str
    detail: str
    source: str
    confidence: Confidence


class ReportCreate(RefcatMixin):
    refcat: str
    email: EmailStr
    # Set by the payments domain; never accepted from a public request.
    payment_reference: str | None = None

    model_config = ConfigDict(json_schema_extra={"example": {"refcat": "24145A00500123"}})


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token: str
    refcat: str
    status: ReportStatus
    created_at: datetime
    generated_at: datetime | None
    download_url: str | None = None
    error_message: str | None = None


class ReportStatusRead(BaseModel):
    """What the buyer polls while the worker is doing its thing."""

    token: str
    refcat: str
    status: ReportStatus
    message: str
    download_url: str | None = None


__all__ = [
    "Confidence",
    "Finding",
    "ReportCreate",
    "ReportRead",
    "ReportStatusRead",
    "REFCAT_PATTERN",
    "Severity",
]
