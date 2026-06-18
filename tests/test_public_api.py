import osoznanie


def test_audited_decision_api_is_exported_from_package_root() -> None:
    expected = {
        "AccessDecisionTrace",
        "AccessEffect",
        "AccessPolicyContent",
        "AccessReasonCode",
        "AccessResource",
        "AccessResourceKind",
        "ActionExecutionError",
        "ActionExecutor",
        "ActionNotAuthorizedError",
        "AuditedDecisionOrchestrator",
        "AuditedDecisionRequest",
        "AuditedDecisionResult",
        "AuditedDecisionStatus",
        "AuthorizationDecision",
        "AuthorizationEngine",
        "AuthorizationQuery",
        "AuthorizedMemoryViewEngine",
        "DecisionCallback",
        "DecisionContext",
        "DecisionContextMismatchError",
        "DecisionProposal",
        "DecisionProposalError",
        "DecisionTrace",
        "DecisionTraceBuildError",
        "DecisionTraceBuilder",
        "DecisionTraceSink",
        "DecisionTraceStorageError",
        "InvalidDecisionTraceProgressionError",
        "OrchestrationError",
        "OutcomeAlreadyAttachedError",
        "OutcomePersistenceError",
        "OutcomeSink",
        "SQLiteAccessPolicyStore",
        "SQLiteAuthorizedMemoryStore",
        "SQLiteDecisionTraceStore",
        "TraceAuthorizationDecision",
        "TracePersistenceError",
    }

    assert expected <= set(osoznanie.__all__)
    assert all(getattr(osoznanie, name) is not None for name in expected)
