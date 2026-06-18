from osoznanie.decision_trace import TraceAuthorizationDecision


def test_trace_authorization_enum() -> None:
    assert TraceAuthorizationDecision.ALLOW.value == "allow"
