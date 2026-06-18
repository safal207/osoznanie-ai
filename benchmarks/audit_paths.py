"""Public and restricted artifacts built from completed decision trials."""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_integrity import (
    ArtifactIntegrityError,
    ArtifactIntegrityIndex,
    PublishedArtifactSet,
    build_integrity_index,
    create_staging_directory,
    publish_staged_set,
    resolve_current_set,
    verify_integrity_index,
    write_text_durable,
)
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
MANIFEST_VERSION = "decision-path-manifest-v0.4"
MANIFEST_FILE_NAME = "decision-path-manifest.json"


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

    def artifact_paths(self) -> tuple[str, str, str]:
        return (
            self.public_json_file,
            self.public_mermaid_file,
            self.audit_json_file,
        )


class AuditPathManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = MANIFEST_VERSION
    evaluated_at: str
    graph_count: int = Field(ge=0)
    entries: list[AuditPathManifestEntry]
    integrity: ArtifactIntegrityIndex | None = None

    @model_validator(mode="after")
    def validate_manifest(self) -> AuditPathManifest:
        if self.graph_count != len(self.entries):
            raise ValueError("graph count must match manifest entries")
        expected_paths = sorted(
            path for entry in self.entries for path in entry.artifact_paths()
        )
        if len(expected_paths) != len(set(expected_paths)):
            raise ValueError("manifest artifact paths must be unique")
        if self.integrity is not None:
            actual_paths = [item.path for item in self.integrity.files]
            if actual_paths != expected_paths:
                raise ValueError(
                    "integrity records must exactly match manifest artifact paths"
                )
        return self


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



def verify_audit_path_set(set_dir: Path) -> AuditPathManifest:
    """Parse and verify one immutable decision-path artifact set."""

    manifest_path = set_dir / MANIFEST_FILE_NAME
    if not manifest_path.is_file():
        raise ArtifactIntegrityError("decision-path manifest is missing")
    manifest = AuditPathManifest.model_validate_json(manifest_path.read_text("utf-8"))
    if manifest.integrity is None:
        raise ArtifactIntegrityError("decision-path manifest has no integrity index")
    verify_integrity_index(set_dir, MANIFEST_FILE_NAME, manifest.integrity)
    return manifest



def verify_published_audit_path_set(publication_root: Path) -> PublishedArtifactSet:
    """Resolve the atomic pointer and verify the currently published set."""

    published = resolve_current_set(publication_root)
    verify_audit_path_set(published.set_dir)
    return published



def write_audit_path_bundle(
    bundle: AuditPathBundle,
    output_dir: Path,
) -> tuple[Path, list[Path]]:
    """Stage, verify, and atomically publish a versioned decision-path set."""

    output_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = create_staging_directory(output_dir)
    relative_paths: list[str] = []
    try:
        for graph, audit, entry in zip(
            bundle.public_graphs,
            bundle.audits,
            bundle.manifest.entries,
            strict=True,
        ):
            payloads = {
                entry.public_json_file: graph.model_dump_json(indent=2) + "\n",
                entry.public_mermaid_file: render_public_mermaid(graph),
                entry.audit_json_file: audit.model_dump_json(indent=2) + "\n",
            }
            for relative_path, payload in payloads.items():
                write_text_durable(staging_dir / relative_path, payload)
                relative_paths.append(relative_path)

        integrity = build_integrity_index(staging_dir, relative_paths)
        published_manifest = bundle.manifest.model_copy(
            update={"integrity": integrity}
        )
        staged_manifest_path = staging_dir / MANIFEST_FILE_NAME
        write_text_durable(
            staged_manifest_path,
            published_manifest.model_dump_json(indent=2) + "\n",
        )
        verify_audit_path_set(staging_dir)
        published = publish_staged_set(
            staging_dir,
            output_dir,
            MANIFEST_FILE_NAME,
        )
        final_paths = [published.set_dir / path for path in sorted(relative_paths)]
        return published.manifest_path, final_paths
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise
