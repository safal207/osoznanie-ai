"""Deterministic decision-path graph construction and rendering."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .fixtures import BENCHMARK_NOW
from .models import StrategyName
from .policies import DecisionPolicy, TopActionableLessonPolicy
from .simulation_fixtures import DecisionSimulationCase
from .simulation_models import DecisionSimulationReport, DecisionTrialResult
from .strategies import DEFAULT_STRATEGIES, RetrievalStrategy

PATH_GRAPH_VERSION = "decision-path-v0.1"
_SLUG_PATTERN = re.compile(r"[^a-z0-9_-]+")


class DecisionPathNodeKind(StrEnum):
    TASK = "task"
    RETRIEVAL = "retrieval"
    LESSON = "lesson"
    POLICY = "policy"
    DECISION = "decision"
    OUTCOME = "outcome"


class DecisionPathStatus(StrEnum):
    SAFE_DECISION = "safe_decision"
    REPEATED_ERROR = "repeated_error"
    ABSTENTION = "abstention"
    OTHER = "other"


class DecisionPathNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    kind: DecisionPathNodeKind
    label: str
    metadata: dict[str, str] = Field(default_factory=dict)
    evaluator_only: bool = False

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("node_id must not be blank")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value


class DecisionPathEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str


class DecisionPathGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_version: str
    graph_id: str
    scenario_id: str
    strategy: StrategyName
    policy_name: str
    status: DecisionPathStatus
    nodes: list[DecisionPathNode] = Field(min_length=1)
    edges: list[DecisionPathEdge]

    @model_validator(mode="after")
    def validate_graph(self) -> DecisionPathGraph:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("decision-path node IDs must be unique")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("decision-path edge IDs must be unique")
        known_nodes = set(node_ids)
        for edge in self.edges:
            if edge.source_node_id not in known_nodes:
                raise ValueError(f"unknown edge source: {edge.source_node_id}")
            if edge.target_node_id not in known_nodes:
                raise ValueError(f"unknown edge target: {edge.target_node_id}")
        return self


class DecisionPathManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_id: str
    scenario_id: str
    strategy: StrategyName
    status: DecisionPathStatus
    json_file: str
    mermaid_file: str


class DecisionPathManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str
    evaluated_at: str
    graph_count: int = Field(ge=0)
    entries: list[DecisionPathManifestEntry]


class DecisionPathBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    simulation_report: DecisionSimulationReport
    graphs: list[DecisionPathGraph]
    manifest: DecisionPathManifest


def _slug(value: str) -> str:
    normalized = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError("value cannot be converted to a stable slug")
    return normalized


def _node_id(graph_id: str, suffix: str) -> str:
    return f"{graph_id}__{suffix}"


def _status(result: DecisionTrialResult) -> DecisionPathStatus:
    if result.abstained:
        return DecisionPathStatus.ABSTENTION
    if result.correct:
        return DecisionPathStatus.SAFE_DECISION
    if result.repeated_error:
        return DecisionPathStatus.REPEATED_ERROR
    return DecisionPathStatus.OTHER


def _metadata(**values: object) -> dict[str, str]:
    return {
        key: str(value)
        for key, value in sorted(values.items())
        if value is not None
    }


def build_decision_path_graph(
    case: DecisionSimulationCase,
    strategy: RetrievalStrategy,
    policy: DecisionPolicy,
    result: DecisionTrialResult,
) -> DecisionPathGraph:
    graph_id = f"{_slug(case.scenario.scenario_id)}--{_slug(strategy.name.value)}"
    ranked = strategy.rank(
        case.retrieval_case.scenario.query,
        case.retrieval_case.store,
        now=BENCHMARK_NOW,
    )

    task_id = _node_id(graph_id, "task")
    retrieval_id = _node_id(graph_id, "retrieval")
    policy_id = _node_id(graph_id, "policy")
    decision_id = _node_id(graph_id, "decision")
    outcome_id = _node_id(graph_id, "outcome")

    nodes = [
        DecisionPathNode(
            node_id=task_id,
            kind=DecisionPathNodeKind.TASK,
            label=f"Task: {case.scenario.task.task_id}",
            metadata=_metadata(
                domain=case.scenario.task.domain,
                task_type=case.scenario.task.task_type,
                default_action=case.scenario.task.default_action_id,
            ),
        ),
        DecisionPathNode(
            node_id=retrieval_id,
            kind=DecisionPathNodeKind.RETRIEVAL,
            label=f"Retrieval: {strategy.name.value}",
            metadata=_metadata(returned_count=len(ranked)),
        ),
    ]
    edges = [
        DecisionPathEdge(
            edge_id=f"{graph_id}__edge_task_retrieval",
            source_node_id=task_id,
            target_node_id=retrieval_id,
            relation="requests_memory",
        )
    ]

    lesson_node_ids: list[str] = []
    for ranked_lesson in sorted(ranked, key=lambda item: item.rank):
        lesson_node_id = _node_id(graph_id, f"lesson_{ranked_lesson.rank}")
        lesson_node_ids.append(lesson_node_id)
        recommendation = case.scenario.recommendations.get(ranked_lesson.lesson_id)
        nodes.append(
            DecisionPathNode(
                node_id=lesson_node_id,
                kind=DecisionPathNodeKind.LESSON,
                label=f"Lesson #{ranked_lesson.rank}: {ranked_lesson.lesson_id}",
                metadata=_metadata(
                    lesson_id=ranked_lesson.lesson_id,
                    rank=ranked_lesson.rank,
                    recommended_action=(
                        recommendation.action_id if recommendation else None
                    ),
                    applied=(
                        ranked_lesson.lesson_id
                        in result.decision.applied_lesson_ids
                    ),
                ),
            )
        )
        edges.append(
            DecisionPathEdge(
                edge_id=f"{graph_id}__edge_retrieval_lesson_{ranked_lesson.rank}",
                source_node_id=retrieval_id,
                target_node_id=lesson_node_id,
                relation="returns",
            )
        )

    nodes.extend(
        [
            DecisionPathNode(
                node_id=policy_id,
                kind=DecisionPathNodeKind.POLICY,
                label=f"Policy: {policy.name}",
                metadata=_metadata(lesson_count=len(ranked)),
            ),
            DecisionPathNode(
                node_id=decision_id,
                kind=DecisionPathNodeKind.DECISION,
                label=(
                    f"Decision: {result.decision.action_id}"
                    if result.decision.action_id
                    else "Decision: abstain"
                ),
                metadata=_metadata(
                    disposition=result.decision.disposition.value,
                    action_id=result.decision.action_id,
                    applied_lessons=",".join(
                        result.decision.applied_lesson_ids
                    ),
                    explanation_codes=",".join(
                        code.value for code in result.decision.explanation_codes
                    ),
                ),
            ),
            DecisionPathNode(
                node_id=outcome_id,
                kind=DecisionPathNodeKind.OUTCOME,
                label=f"Outcome: {_status(result).value}",
                metadata=_metadata(
                    correct=result.correct,
                    repeated_error=result.repeated_error,
                    abstained=result.abstained,
                ),
                evaluator_only=True,
            ),
        ]
    )

    if lesson_node_ids:
        for index, lesson_node_id in enumerate(lesson_node_ids, start=1):
            edges.append(
                DecisionPathEdge(
                    edge_id=f"{graph_id}__edge_lesson_{index}_policy",
                    source_node_id=lesson_node_id,
                    target_node_id=policy_id,
                    relation="considered_by",
                )
            )
    else:
        edges.append(
            DecisionPathEdge(
                edge_id=f"{graph_id}__edge_retrieval_policy",
                source_node_id=retrieval_id,
                target_node_id=policy_id,
                relation="returns_empty",
            )
        )

    edges.extend(
        [
            DecisionPathEdge(
                edge_id=f"{graph_id}__edge_policy_decision",
                source_node_id=policy_id,
                target_node_id=decision_id,
                relation="selects",
            ),
            DecisionPathEdge(
                edge_id=f"{graph_id}__edge_decision_outcome",
                source_node_id=decision_id,
                target_node_id=outcome_id,
                relation="evaluated_as",
            ),
        ]
    )

    return DecisionPathGraph(
        graph_version=PATH_GRAPH_VERSION,
        graph_id=graph_id,
        scenario_id=case.scenario.scenario_id,
        strategy=strategy.name,
        policy_name=policy.name,
        status=_status(result),
        nodes=nodes,
        edges=edges,
    )


def build_decision_path_bundle(
    cases: list[DecisionSimulationCase],
    simulation_report: DecisionSimulationReport,
    strategies: tuple[RetrievalStrategy, ...] = DEFAULT_STRATEGIES,
    policy: DecisionPolicy | None = None,
) -> DecisionPathBundle:
    effective_policy = policy or TopActionableLessonPolicy()
    result_index = {
        (result.scenario_id, result.strategy): result
        for result in simulation_report.trial_results
    }
    graphs = [
        build_decision_path_graph(
            case,
            strategy,
            effective_policy,
            result_index[(case.scenario.scenario_id, strategy.name)],
        )
        for strategy in strategies
        for case in cases
    ]
    entries = [
        DecisionPathManifestEntry(
            graph_id=graph.graph_id,
            scenario_id=graph.scenario_id,
            strategy=graph.strategy,
            status=graph.status,
            json_file=f"graphs/{graph.graph_id}.json",
            mermaid_file=f"graphs/{graph.graph_id}.mmd",
        )
        for graph in graphs
    ]
    manifest = DecisionPathManifest(
        manifest_version=PATH_GRAPH_VERSION,
        evaluated_at=BENCHMARK_NOW.isoformat(),
        graph_count=len(graphs),
        entries=entries,
    )
    return DecisionPathBundle(
        simulation_report=simulation_report,
        graphs=graphs,
        manifest=manifest,
    )


def _escape_mermaid(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")


def render_mermaid(graph: DecisionPathGraph) -> str:
    lines = [
        "flowchart LR",
        f"%% graph_id: {graph.graph_id}",
        f"%% status: {graph.status.value}",
    ]
    for node in graph.nodes:
        label = _escape_mermaid(node.label)
        if node.kind is DecisionPathNodeKind.POLICY:
            declaration = f'{node.node_id}{{"{label}"}}'
        elif node.kind is DecisionPathNodeKind.OUTCOME:
            declaration = f'{node.node_id}(["{label}"])'
        else:
            declaration = f'{node.node_id}["{label}"]'
        lines.append(f"    {declaration}")
    for edge in graph.edges:
        relation = _escape_mermaid(edge.relation)
        lines.append(
            f"    {edge.source_node_id} -->|{relation}| {edge.target_node_id}"
        )
    return "\n".join(lines) + "\n"


def write_decision_path_bundle(
    bundle: DecisionPathBundle,
    output_dir: Path,
) -> tuple[Path, list[Path]]:
    graph_dir = output_dir / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for graph in bundle.graphs:
        json_path = graph_dir / f"{graph.graph_id}.json"
        mermaid_path = graph_dir / f"{graph.graph_id}.mmd"
        json_path.write_text(
            graph.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        mermaid_path.write_text(render_mermaid(graph), encoding="utf-8")
        written.extend([json_path, mermaid_path])

    manifest_path = output_dir / "decision-path-manifest.json"
    manifest_path.write_text(
        bundle.manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path, written
