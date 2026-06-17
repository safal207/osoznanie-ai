import json
from pathlib import Path

from jsonschema import Draft202012Validator

from osoznanie.models import (
    Commitment,
    Decision,
    Event,
    Evidence,
    Hypothesis,
    IdentitySnapshot,
    Lesson,
    Outcome,
    OutcomeStatus,
    Reflection,
    Trait,
)
from osoznanie.schema import schema_documents, sync_schemas

SCHEMA_DIR = Path("schemas")


def sample_records():
    return {
        "evidence.schema.json": Evidence(source_type="test-report", uri="demo://report/1"),
        "event.schema.json": Event(actor_ids=["agent_qa"], summary="A checkout test ran."),
        "decision.schema.json": Decision(
            event_id="evt_1",
            agent_id="agent_qa",
            chosen_action="Approve the release.",
            reasoning_summary="The selected checks passed.",
            confidence=0.7,
        ),
        "outcome.schema.json": Outcome(
            decision_id="dec_1",
            status=OutcomeStatus.FAILURE,
            summary="Checkout failed on Android Chrome.",
        ),
        "reflection.schema.json": Reflection(
            source_ids=["evt_1", "dec_1", "out_1"],
            hypotheses=[
                Hypothesis(
                    statement="The mobile browser matrix was omitted.",
                    confidence=0.9,
                )
            ],
        ),
        "lesson.schema.json": Lesson(
            statement="Test supported mobile browsers before approving checkout releases.",
            source_reflection_ids=["ref_1"],
            confidence=0.88,
        ),
        "commitment.schema.json": Commitment(
            agent_id="agent_qa",
            counterparty_ids=["human_alexey"],
            statement="Add Android Chrome to the next regression plan.",
        ),
        "trait.schema.json": Trait(
            name="cross-platform caution",
            description="Prefers representative device coverage for critical flows.",
            value=0.72,
            confidence=0.81,
        ),
        "identity-snapshot.schema.json": IdentitySnapshot(
            agent_id="agent_qa",
            version=1,
            change_summary="Created the first accountable identity snapshot.",
        ),
    }


def test_schema_generation_is_deterministic() -> None:
    assert schema_documents() == schema_documents()


def test_committed_schemas_are_current() -> None:
    assert sync_schemas(SCHEMA_DIR, check=True) == []


def test_examples_validate_against_public_schemas() -> None:
    for filename, record in sample_records().items():
        schema = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(record.model_dump(mode="json"))
