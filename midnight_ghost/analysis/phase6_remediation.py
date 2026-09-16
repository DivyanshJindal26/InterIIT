from __future__ import annotations

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.query.types import TimeRange
from midnight_ghost.analysis.types import (
    IncidentWindow,
    RemediationAction,
    RootCause,
    ValidationResult,
)

ACTION_CATALOG = {
    'deployment': {'action': 'rollback', 'params': {'to': 'previous_version'}},
    'exhaustion': {'action': 'scale_up', 'params': {'replicas': '+2'}},
    'external': {'action': 'circuit_break', 'params': {'timeout': 30}},
    'drift': {'action': 'sync_config', 'params': {'source': 'canonical'}},
    'unknown': {'action': 'isolate', 'params': {'drain': True}},
}

BLAST_THRESHOLD = 0.6


def _get_downstream_cone(service: str, dependency_graph: dict) -> set[str]:
    visited: set[str] = set()
    queue = [service]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        deps = dependency_graph.get(current, {})
        if isinstance(deps, dict):
            neighbors = deps.get('dependencies', [])
        elif isinstance(deps, list):
            neighbors = deps
        else:
            neighbors = []
        for neighbor in neighbors:
            queue.append(neighbor)
    return visited


def plan_remediation(
    root_cause: RootCause,
    dependency_graph: dict,
) -> RemediationAction:
    action_spec = ACTION_CATALOG.get(
        root_cause.failure_mode, ACTION_CATALOG['unknown'],
    )

    downstream = _get_downstream_cone(root_cause.service, dependency_graph)
    total_services = len(dependency_graph)
    blast_radius = len(downstream) / max(total_services, 1)

    safe = blast_radius < BLAST_THRESHOLD

    return RemediationAction(
        action_type=action_spec['action'],
        target_service=root_cause.service,
        params=action_spec['params'],
        blast_radius=blast_radius,
        executed=safe,
        safe=safe,
    )


def validate_remediation(
    query_api: QueryAPI,
    root_cause: RootCause,
    remediation: RemediationAction,
    incident_window: IncidentWindow,
    cascade_participants: list[str],
) -> ValidationResult:
    total_duration = incident_window.incident.end_ns - incident_window.incident.start_ns
    val_start = incident_window.incident.end_ns - int(total_duration * 0.2)
    val_window = TimeRange(val_start, incident_window.incident.end_ns)

    root_summary = query_api.get_anomaly_summary(root_cause.service, val_window)
    anomaly_after = root_summary.anomaly_score

    downstream_ok = True
    for svc in cascade_participants:
        if svc == root_cause.service:
            continue
        svc_summary = query_api.get_anomaly_summary(svc, val_window)
        if svc_summary.anomaly_score > 0.3:
            downstream_ok = False
            break

    if anomaly_after < 0.2 and downstream_ok:
        status = 'resolved'
    elif anomaly_after >= 0.2:
        status = 'escalate'
    else:
        status = 'regression'

    return ValidationResult(
        status=status,
        anomaly_before=1.0,
        anomaly_after=anomaly_after,
        downstream_recovered=downstream_ok,
    )
