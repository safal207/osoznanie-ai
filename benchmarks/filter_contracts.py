"""Privacy-aware projections of aggregate recall filter diagnostics."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osoznanie.recall import RecallFilterCounts


class CountVisibility(StrEnum):
    DISCLOSED = "disclosed"
    REDACTED = "redacted"


class AuditedCount(BaseModel):
    """A count whose disclosure state is explicit and internally consistent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visibility: CountVisibility
    value: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def check_contract(self) -> AuditedCount:
        if self.visibility is CountVisibility.DISCLOSED and self.value is None:
            raise ValueError("disclosed count requires a non-null value")
        if self.visibility is CountVisibility.REDACTED and self.value is not None:
            raise ValueError("redacted count requires a null value")
        return self

    @classmethod
    def disclosed(cls, value: int) -> AuditedCount:
        return cls(visibility=CountVisibility.DISCLOSED, value=value)

    @classmethod
    def redacted(cls) -> AuditedCount:
        return cls(visibility=CountVisibility.REDACTED, value=None)


class FilterSummary(BaseModel):
    """Aggregate first-exclusion counters with visibility applied per field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_rejected: AuditedCount
    not_yet_effective: AuditedCount
    expired: AuditedCount
    domain_mismatch: AuditedCount
    insufficient_scope: AuditedCount
    access_denied: AuditedCount
    below_risk_threshold: AuditedCount

    @classmethod
    def public(cls, counts: RecallFilterCounts) -> FilterSummary:
        return cls(
            validation_rejected=AuditedCount.disclosed(counts.validation_rejected),
            not_yet_effective=AuditedCount.disclosed(counts.not_yet_effective),
            expired=AuditedCount.disclosed(counts.expired),
            domain_mismatch=AuditedCount.disclosed(counts.domain_mismatch),
            insufficient_scope=AuditedCount.disclosed(counts.insufficient_scope),
            access_denied=AuditedCount.redacted(),
            below_risk_threshold=AuditedCount.disclosed(
                counts.below_risk_threshold
            ),
        )

    @classmethod
    def restricted(cls, counts: RecallFilterCounts) -> FilterSummary:
        return cls(
            validation_rejected=AuditedCount.disclosed(counts.validation_rejected),
            not_yet_effective=AuditedCount.disclosed(counts.not_yet_effective),
            expired=AuditedCount.disclosed(counts.expired),
            domain_mismatch=AuditedCount.disclosed(counts.domain_mismatch),
            insufficient_scope=AuditedCount.disclosed(counts.insufficient_scope),
            access_denied=AuditedCount.disclosed(counts.access_denied),
            below_risk_threshold=AuditedCount.disclosed(
                counts.below_risk_threshold
            ),
        )
