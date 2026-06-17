"""Shared decision-path classification contracts."""

from __future__ import annotations

from enum import StrEnum


class DecisionPathStatus(StrEnum):
    SAFE_DECISION = "safe_decision"
    REPEATED_ERROR = "repeated_error"
    ABSTENTION = "abstention"
    ALTERNATE_ACTION = "alternate_action"


class DecisionPathReasonCode(StrEnum):
    SAFE_ACTION_SELECTED = "safe_action_selected"
    REPEATED_ERROR_ACTION_SELECTED = "repeated_error_action_selected"
    POLICY_ABSTAINED = "policy_abstained"
    NON_REFERENCE_ACTION_SELECTED = "non_reference_action_selected"


_STATUS_REASON_PAIRS = {
    DecisionPathStatus.SAFE_DECISION: DecisionPathReasonCode.SAFE_ACTION_SELECTED,
    DecisionPathStatus.REPEATED_ERROR: (
        DecisionPathReasonCode.REPEATED_ERROR_ACTION_SELECTED
    ),
    DecisionPathStatus.ABSTENTION: DecisionPathReasonCode.POLICY_ABSTAINED,
    DecisionPathStatus.ALTERNATE_ACTION: (
        DecisionPathReasonCode.NON_REFERENCE_ACTION_SELECTED
    ),
}


def classify_decision_path(
    *,
    correct: bool,
    repeated_error: bool,
    abstained: bool,
) -> tuple[DecisionPathStatus, DecisionPathReasonCode]:
    if abstained:
        return (
            DecisionPathStatus.ABSTENTION,
            DecisionPathReasonCode.POLICY_ABSTAINED,
        )
    if correct:
        return (
            DecisionPathStatus.SAFE_DECISION,
            DecisionPathReasonCode.SAFE_ACTION_SELECTED,
        )
    if repeated_error:
        return (
            DecisionPathStatus.REPEATED_ERROR,
            DecisionPathReasonCode.REPEATED_ERROR_ACTION_SELECTED,
        )
    return (
        DecisionPathStatus.ALTERNATE_ACTION,
        DecisionPathReasonCode.NON_REFERENCE_ACTION_SELECTED,
    )


def validate_status_reason(
    status: DecisionPathStatus,
    reason_code: DecisionPathReasonCode,
) -> None:
    expected = _STATUS_REASON_PAIRS[status]
    if reason_code is not expected:
        raise ValueError(
            f"reason code {reason_code.value!r} does not match status {status.value!r}"
        )
