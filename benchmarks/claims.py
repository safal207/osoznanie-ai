"""Structured claim boundaries for synthetic benchmark reports."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClaimScope(StrEnum):
    SYNTHETIC_FIXTURES_ONLY = "synthetic-fixtures-only"


class PolicyKind(StrEnum):
    DETERMINISTIC_RETRIEVAL_EVALUATOR = "deterministic-retrieval-evaluator"
    DETERMINISTIC_REFERENCE_POLICY = "deterministic-reference-policy"


DEFAULT_DISCLAIMER = (
    "Results describe deterministic behavior on authored synthetic fixtures. "
    "They do not measure real LLM behavioral impact, live-agent improvement, "
    "or real-world incident reduction."
)


class SyntheticClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: ClaimScope = ClaimScope.SYNTHETIC_FIXTURES_ONLY
    fixture_count: int = Field(ge=1)
    policy_kind: PolicyKind
    disclaimer: str = DEFAULT_DISCLAIMER

    @field_validator("disclaimer")
    @classmethod
    def validate_disclaimer(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("disclaimer must not be blank")
        return normalized


def build_synthetic_claim(
    fixture_count: int,
    policy_kind: PolicyKind,
) -> SyntheticClaim:
    return SyntheticClaim(fixture_count=fixture_count, policy_kind=policy_kind)
