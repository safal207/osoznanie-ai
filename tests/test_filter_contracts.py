import pytest
from pydantic import ValidationError

from benchmarks.filter_contracts import AuditedCount, CountVisibility, FilterSummary
from osoznanie.recall import RecallFilterCounts


def test_count_contract_rejects_invalid_states() -> None:
    with pytest.raises(ValidationError):
        AuditedCount(visibility=CountVisibility.DISCLOSED, value=None)
    with pytest.raises(ValidationError):
        AuditedCount(visibility=CountVisibility.REDACTED, value=1)


def test_filter_summary_visibility() -> None:
    counts = RecallFilterCounts(access_denied=2)
    public = FilterSummary.public(counts)
    restricted = FilterSummary.restricted(counts)

    assert public.access_denied.value is None
    assert restricted.access_denied.value == 2
