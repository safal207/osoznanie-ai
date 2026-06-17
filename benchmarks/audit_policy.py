"""Validated ranking-policy references for benchmark audit artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from osoznanie.recall import get_ranking_policy

from .models import StrategyName


@dataclass(frozen=True)
class _PolicySpec:
    id: str
    score_formula_version: str
    score_bucket_width: Decimal
    score_order: str
    tie_break_fields: tuple[str, ...]


_recall = get_ranking_policy()
_POLICY_SPECS: Mapping[str, _PolicySpec] = MappingProxyType(
    {
        "naive-keyword-ranking-v0.1": _PolicySpec(
            id="naive-keyword-ranking-v0.1",
            score_formula_version="keyword-overlap-v0.1",
            score_bucket_width=Decimal("0.000001"),
            score_order="desc",
            tie_break_fields=("lesson_id",),
        ),
        _recall.id: _PolicySpec(
            id=_recall.id,
            score_formula_version=_recall.score_formula_version,
            score_bucket_width=_recall.score_bucket_width,
            score_order=_recall.score_order,
            tie_break_fields=_recall.tie_break_fields,
        ),
    }
)


class RankingPolicyRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    score_formula_version: str = Field(min_length=1)
    score_bucket_width: Decimal
    score_order: str = Field(min_length=1)
    tie_break_fields: tuple[str, ...] = Field(min_length=1)

    @field_serializer("score_bucket_width")
    def serialize_width(self, value: Decimal) -> str:
        return format(value, "f")

    @model_validator(mode="after")
    def validate_policy(self) -> "RankingPolicyRef":
        expected = _POLICY_SPECS.get(self.id)
        if expected is None:
            raise ValueError(f"unknown ranking policy: {self.id}")
        actual = _PolicySpec(
            id=self.id,
            score_formula_version=self.score_formula_version,
            score_bucket_width=self.score_bucket_width,
            score_order=self.score_order,
            tie_break_fields=self.tie_break_fields,
        )
        if actual != expected:
            raise ValueError(f"configuration does not match policy {self.id}")
        return self


def ranking_policy_ref_for(strategy: StrategyName) -> RankingPolicyRef | None:
    if strategy is StrategyName.NO_MEMORY:
        return None
    policy_id = (
        "naive-keyword-ranking-v0.1"
        if strategy is StrategyName.NAIVE_KEYWORD
        else _recall.id
    )
    spec = _POLICY_SPECS[policy_id]
    return RankingPolicyRef(
        id=spec.id,
        score_formula_version=spec.score_formula_version,
        score_bucket_width=spec.score_bucket_width,
        score_order=spec.score_order,
        tie_break_fields=spec.tie_break_fields,
    )
