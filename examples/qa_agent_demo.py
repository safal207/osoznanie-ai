"""Tiny executable demonstration of Osoznanie's first experience loop."""

from osoznanie.models import (
    Decision,
    Evidence,
    Event,
    Hypothesis,
    Lesson,
    Outcome,
    OutcomeStatus,
    Reflection,
    TrustLevel,
    ValidationStatus,
)
from osoznanie.storage import SQLiteExperienceStore


def main() -> None:
    with SQLiteExperienceStore("osoznanie-demo.db") as store:
        evidence = store.save(
            Evidence(
                source_type="incident-report",
                uri="demo://checkout/android-chrome",
                trust_level=TrustLevel.VERIFIED,
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
                scope={"task": "checkout-release-validation"},
                source_reflection_ids=[reflection.id],
                confidence=0.88,
                validation_status=ValidationStatus.HUMAN_APPROVED,
            )
        )

        print("Validated lesson:")
        print(lesson.statement)
        print("\nWhy it exists:")
        for source in store.explain(lesson.id)["references"]:
            print(f"- {source['type']}: {source['id']}")


if __name__ == "__main__":
    main()
