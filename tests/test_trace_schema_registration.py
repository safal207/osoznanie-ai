from osoznanie.schema import PUBLIC_SCHEMA_MODELS, schema_documents


def test_decision_trace_schema_is_public() -> None:
    assert "decision_trace" in PUBLIC_SCHEMA_MODELS
    assert "decision-trace.schema.json" in schema_documents()
