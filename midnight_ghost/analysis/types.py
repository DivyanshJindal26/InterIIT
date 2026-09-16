from __future__ import annotations

from dataclasses import dataclass, field

from midnight_ghost.query.types import AnomalySummary, TimeRange


@dataclass
class IncidentWindow:
    baseline: TimeRange
    incident: TimeRange
    alert_time_ns: int


@dataclass
class BlameEdge:
    accuser: str
    accused: str
    weight: float
    source: str
    confidence: float


@dataclass
class RecoveryInfo:
    service: str
    recovery_time_ns: int | None
    recovery_type: str
    trajectory: str
    error_rate_before: float
    error_rate_after: float
    throughput_before: float
    throughput_after: float


@dataclass
class CausalCandidate:
    service: str
    suspicion_score: float
    blamed_by: list[BlameEdge]
    has_config_change: bool
    recovery_info: RecoveryInfo | None
    falsified: bool
    falsification_reason: str


@dataclass
class RootCause:
    service: str
    confidence: float
    failure_mode: str
    evidence: list[str]
    propagation_path: list[str]
    onset_time_ns: int | None


@dataclass
class RemediationAction:
    action_type: str
    target_service: str
    params: dict
    blast_radius: float
    executed: bool
    safe: bool


@dataclass
class ValidationResult:
    status: str
    anomaly_before: float
    anomaly_after: float
    downstream_recovered: bool


@dataclass
class IncidentReport:
    root_cause: RootCause
    candidates: list[CausalCandidate]
    remediation: RemediationAction
    validation: ValidationResult
    failure_mode: str
    incident_window: IncidentWindow
    budget_used: int
    budget_log: list[dict]
    signal_agreement: dict


@dataclass
class Phase0Result:
    incident_window: IncidentWindow
    cascade_participants: list[str]
    anomaly_summaries: dict[str, AnomalySummary]
    primary_symptom: str


@dataclass
class Phase05Result:
    blame_edges: list[BlameEdge]


@dataclass
class Phase1Result:
    failure_mode: str
    initial_spike: float
    sustained_slope: float
