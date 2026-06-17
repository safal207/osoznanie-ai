"""Executable demonstration of Osoznanie's first experience and recall loop."""

from osoznanie.models import (
    AccessPolicy,
    Decision,
    Event,
    Evidence,
    Hypothesis,
    Lesson,
    Outcome,
    OutcomeStatus,
    Reflection,
    TrustLevel,
    ValidationStatus,
)
from osoznanie.recall import RecallEngine, RecallQuery, RiskLevel
from osoznanie.storage import SQLiteExperienceStore


def main() -> None:
    with SQLiteExperienceStore("osoznanie-demo.db") as store:
        evidence = store.save(
            Evidence(
                source_type="incident-report",
                uri="demo://checkout/android-chrome",
                trust_level=TrustLevel.VERIFIED,
                access_policy=AccessPolicy.OWNER_AND_AGENT,
                owner_id="human_alexey",
                agent_id="agent_qa",
            )
        )
        event = store.save(
            Event(
                actor_ids=["agent_qa", "human_alexey"],
                summary="Checkout failed for customers on Android Chrome.",
                evidence_ids=[evidence.id],
            )
        )
        decision = store.save(
            Decision(
                event_id=event.id,
                agent_id="agent_qa",
                chosen_action="Approve after desktop Chrome smoke test.",
                alternatives_considered=["Test the supported browser-device matrix."],
                reasoning_summary="Desktop tests passed.",
                evidence_ids=[evidence.id],
                confidence=0.63,
            )
        )
        outcome = store.save(
            Outcome(
                decision_id=decision.id,
                status=OutcomeStatus.FAILURE,
                summary="The release blocked checkout on a supported mobile configuration.",
                evidence_ids=[evidence.id],
            )
        )
        reflection = store.save(
            Reflection(
                source_ids=[event.id, decision.id, outcome.id],
                hypotheses=[
                    Hypothesis(
                        statement="The release scope omitted Android Chrome coverage.",
                        confidence=0.92,
                        evidence_ids=[evidence.id],
                    )
                ],
                validation_status=ValidationStatus.HUMAN_APPROVED,
            )
        )
        lesson = store.save(
            Lesson(
                statement=(
                    "Test the supported browser-device matrix before approving "
                    "customer-critical checkout changes."
                ),
                scope={
                    "domain": "quality-assurance",
                    "task_types": ["checkout-release-validation"],
                    "tags": ["checkout", "chrome", "release"],
                },
                source_reflection_ids=[reflection.id],
                confidence=0.88,
                validation_status=ValidationStatus.HUMAN_APPROVED,
            )
        )

        results = RecallEngine(store).recall(
            RecallQuery(
                agent_id="agent_qa",
                requester_id="human_alexey",
                domain="quality-assurance",
                task_type="checkout-release-validation",
                tags=["checkout", "chrome"],
                risk_level=RiskLevel.HIGH,
            )
        )

        print("Validated lesson:")
        print(lesson.statement)
        print("\nRecalled before the next release:")
        for result in results:
            print(f"- score={result.score}: {result.statement}")
            print(f"  {result.explanation}")


if __name__ == "__main__":
    main()
