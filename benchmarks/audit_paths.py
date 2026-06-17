"""Public and restricted artifacts built from completed decision trials."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audit_contracts import AuditRetrievedLesson, RestrictedDecisionPathAudit
from .audit_policy import ranking_policy_ref_for
from .decision_paths import (
    DecisionPathEdge,
    DecisionPathNode,
    DecisionPathNodeKind,
    build_decision_path_graph,
)
from .filter_contracts import FilterSummary
from .models import StrategyName
from .path_contracts import (
    DecisionPathReasonCode,
    DecisionPathStatus,
    classify_decision_path,
    validate_status_reason,
)
from .policies import DecisionPolicy, TopActionableLessonPolicy
from .report_contracts import (
    AuditedDecisionTrialResult,
    StructuredDecisionSimulationReport,
)
from .simulation_fixtures import DecisionSimulationCase
from .strategies import DEFAULT_STRATEGIES, RetrievalStrategy

PUBLIC_PATH_VERSION = "decision-path-public-v0.3"
MANIFEST_VERSION = "decision-path-manifest-v0.3"


class PublicDecisionPathArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_version: str = PUBLIC_PATH_VERSION
    graph_id: str
    scenario_id: str
    strategy: StrategyName
    policy_name: str
    status: DecisionPathStatus
    reason_code: DecisionPathReasonCode
    filter_summary: FilterSummary | None = None
    nodes: list[DecisionPathNode] = Field(min_length=1)
    edges: list[DecisionPathEdge]

    @model_validator(mode="after")
    def validate_classification(self) -> PublicDecisionPathArtifact:
        validate_status_reason(self.status, self.reason_code)
        if self.strategy is StrategyName.OSOZNANIE_RECALL:
            if self.filter_summary is None:
                raise ValueError("Osoznanie public artifact requires filter summary")
        elif self.filter_summary is not None:
            raise ValueError(
                "strategies without a structured filter pipeline require null summary"
            )
        return self


class AuditPathManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_id: str
    scenario_id: str
    strategy: StrategyName
    status: DecisionPathStatus
    reason_code: DecisionPathReasonCode
    public_json_file: str
    public_mermaid_file: str
    audit_json_file: str


class AuditPathManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = MANIFEST_VERSION
    evaluated_at: str
    graph_count: int = Field(ge=0)
    entries: list[AuditPathManifestEntry]


class AuditPathBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    simulation_report: StructuredDecisionSimulationReport
    public_graphs: list[PublicDecisionPathArtifact]
    audits: list[RestrictedDecisionPathAudit]
    manifest: AuditPathManifest


def _classification(
    result: AuditedDecisionTrialResult,
) -> tuple[DecisionPathStatus, DecisionPathReasonCode]:
    return classify_decision_path(
        correct=result.correct,
        repeated_error=result.repeated_error,
        abstained=result.abstained,
    )


def _public_graph(
    case: DecisionSimulationCase,
    strategy: RetrievalStrategy,
    policy: DecisionPolicy,
    result: AuditedDecisionTrialResult,
) -> PublicDecisionPathArtifact:
    legacy = build_decision_path_graph(case, strategy, policy, result)
    status, reason_code = _classification(result)
    nodes = []
    for node in legacy.nodes:
        if node.kind is DecisionPathNodeKind.OUTCOME:
            metadata = dict(node.metadata)
            metadata["reason_code"] = reason_code.value
            node = node.model_copy(
                update={
                    "label": f"Outcome: {status.value}",
                    "metadata": metadata,
                }
            )
        nodes.append(node)
    return PublicDecisionPathArtifact(
        graph_id=legacy.graph_id,
        scenario_id=legacy.scenario_id,
        strategy=legacy.strategy,
        policy_name=legacy.policy_name,
        status=status,
        reason_code=reason_code,
        filter_summary=(
            FilterSummary.public(result.filter_counts)
            if result.filter_counts is not None
            else None
        ),
        nodes=nodes,
        edges=legacy.edges,
    )


def _restricted_audit(
    graph: PublicDecisionPathArtifact,
    report: StructuredDecisionSimulationReport,
    result: AuditedDecisionTrialResult,
) -> RestrictedDecisionPathAudit:
    return RestrictedDecisionPathAudit(
        graph_id=graph.graph_id,
        scenario_id=graph.scenario_id,
        strategy=graph.strategy,
        policy_name=graph.policy_name,
        claim=report.claim,
        ranking_policy=ranking_policy_ref_for(graph.strategy),
        filter_summary=(
            FilterSummary.restricted(result.filter_counts)
            if result.filter_counts is not None
            else None
        ),
        returned_lessons=[
            AuditRetrievedLesson.from_snapshot(item)
            for item in result.returned_lessons
        ],
        decision=result.decision,
        status=graph.status,
        reason_code=graph.reason_code,
    )


def build_audit_path_bundle(
    cases: list[DecisionSimulationCase],
    report: StructuredDecisionSimulationReport,
    strategies: tuple[RetrievalStrategy, ...] = DEFAULT_STRATEGIES,
    policy: DecisionPolicy | None = None,
) -> AuditPathBundle:
    effective_policy = policy or TopActionableLessonPolicy()
    result_index = {
        (result.scenario_id, result.strategy): result
        for result in report.trial_results
    }
    graph_result_pairs = [
        (
            _public_graph(
                case,
                strategy,
                effective_policy,
                result_index[(case.scenario.scenario_id, strategy.name)],
            ),
            result_index[(case.scenario.scenario_id, strategy.name)],
        )
        for strategy in strategies
        for case in cases
    ]
    public_graphs = [graph for graph, _ in graph_result_pairs]
    audits = [
        _restricted_audit(graph, report, result)
        for graph, result in graph_result_pairs
    ]
    entries = [
        AuditPathManifestEntry(
            graph_id=graph.graph_id,
            scenario_id=graph.scenario_id,
            strategy=graph.strategy,
            status=graph.status,
            reason_code=graph.reason_code,
            public_json_file=f"graphs/{graph.graph_id}.public.json",
            public_mermaid_file=f"graphs/{graph.graph_id}.public.mmd",
            audit_json_file=f"graphs/{graph.graph_id}.audit.json",
        )
        for graph in public_graphs
    ]
    return AuditPathBundle(
        simulation_report=report,
        public_graphs=public_graphs,
        audits=audits,
        manifest=AuditPathManifest(
            evaluated_at=report.evaluated_at.isoformat(),
            graph_count=len(public_graphs),
            entries=entries,
        ),
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")


def render_public_mermaid(graph: PublicDecisionPathArtifact) -> str:
    lines = [
        "flowchart LR",
        f"%% graph_id: {graph.graph_id}",
        f"%% status: {graph.status.value}",
        f"%% reason_code: {graph.reason_code.value}",
    ]
    for node in graph.nodes:
        label = _escape(node.label)
        if node.kind is DecisionPathNodeKind.POLICY:
            declaration = f'{node.node_id}{{"{label}"}}'
        elif node.kind is DecisionPathNodeKind.OUTCOME:
            declaration = f'{node.node_id}(["{label}"])'
        else:
            declaration = f'{node.node_id}["{label}"]'
        lines.append(f"    {declaration}")
    for edge in graph.edges:
        lines.append(
            f"    {edge.source_node_id} -->|{_escape(edge.relation)}| "
            f"{edge.target_node_id}"
        )
    return "\n".join(lines) + "\n"


def write_audit_path_bundle(
    bundle: AuditPathBundle,
    output_dir: Path,
) -> tuple[Path, list[Path]]:
    graph_dir = output_dir / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for graph, audit in zip(bundle.public_graphs, bundle.audits, strict=True):
        public_json = graph_dir / f"{graph.graph_id}.public.json"
        public_mermaid = graph_dir / f"{graph.graph_id}.public.mmd"
        audit_json = graph_dir / f"{graph.graph_id}.audit.json"
        public_json.write_text(
            graph.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        public_mermaid.write_text(render_public_mermaid(graph), encoding="utf-8")
        audit_json.write_text(
            audit.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        written.extend([public_json, public_mermaid, audit_json])
    manifest_path = output_dir / "decision-path-manifest.json"
    manifest_path.write_text(
        bundle.manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path, written
