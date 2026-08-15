from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 14 characters for a rural parcel, 20 with the property control digits. The frontend
# validates the same shape before sending the user to Stripe (see OrderForm.astro).
REFCAT_PATTERN = r"^[A-Z0-9]{14}$|^[A-Z0-9]{20}$"


class RefcatMixin(BaseModel):
    @field_validator("refcat", mode="before", check_fields=False)
    @classmethod
    def normalize_refcat(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "") if isinstance(value, str) else value


class Subplot(BaseModel):
    crop: str = ""
    intensity: str = ""
    area_m2: float = 0.0


class ParcelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    refcat: str
    municipality: str
    province: str
    use: str
    cadastral_area_m2: float
    built_area_m2: float
    measured_area_m2: float
    subplots: list[Subplot]
    lat: float
    lon: float
    refreshed_at: datetime | None


class AreaComparison(BaseModel):
    """Declared vs measured area — the discrepancy the seller never mentions."""

    cadastral_area_m2: float
    measured_area_m2: float
    difference_ratio: float
    is_significant: bool = Field(
        description="True when the gap exceeds 5%, the threshold worth verifying"
    )


class ParcelPreview(RefcatMixin):
    """Free preview: enough to be useful and indexable, not enough to replace the report.

    It states identification, area coherence and which layers were checked. What it never
    does is give the dictamen: findings, severity and recommendations are the paid product.
    """

    refcat: str
    municipality: str
    province: str
    use: str
    cadastral_area_m2: float
    lat: float
    lon: float
    area: AreaComparison
    subplots: list[Subplot]
    checked_layers: list[str]
    unavailable_layers: list[str]
    included_in_full_report: list[str]
    sources: list[str]
    disclaimer: str
