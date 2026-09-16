from __future__ import annotations

from collections import defaultdict

from midnight_ghost.analysis.types import (
    BlameEdge,
    CausalCandidate,
    IncidentWindow,
)
from midnight_ghost.query.types import AnomalySummary, TimeRange

ALPHA = 1.5

CONFIG_CHANGE_REASONS = {
    'Deployed', 'Started', 'ConfigChanged', 'Rolled',
    'RollingUpdate', 'ScalingReplicaSet',
    'DeploymentUpdated', 'RollbackTriggered',
}


def _detect_config_changes(
    k8s_events: list[dict],
    incident_window: IncidentWindow,
) -> set[str]:
    changed: set[str] = set()
    pre_start = incident_window.incident.start_ns - 10 * 60 * 1_000_000_000
    check_end = incident_window.incident.end_ns

    for event in k8s_events:
        reason = event.get('reason', '')
        if reason in CONFIG_CHANGE_REASONS:
            ts = event.get('timestamp_ns', 0)
            if pre_start <= ts <= check_end:
                changed.add(event.get('service', ''))
    return changed


def _compute_depth(service: str, call_graph: dict[str, list[str]]) -> int:
    callers: dict[str, list[str]] = defaultdict(list)
    for caller, callees in call_graph.items():
        for callee in callees:
            callers[callee].append(caller)

    if service not in callers:
        if service in call_graph and call_graph[service]:
            return 0
        return 1

    depth = 0
    visited = set()
    current = service
    while current in callers and current not in visited:
        visited.add(current)
        current = callers[current][0]
        depth += 1
    return depth


def _get_metric_distress(
    query_api,
    service: str,
    incident_window: IncidentWindow,
) -> float:
    from midnight_ghost.query.types import TimeRange
    distress = 0.0

    lat_series = query_api.get_metric_series(
        service, 'latency_multiplier', incident_window.incident,
    )
    if lat_series:
        peak_lat = max(v for _, v in lat_series)
        if peak_lat > 5:
            distress += min(1.0, peak_lat / 100.0)

    err_series = query_api.get_metric_series(
        service, 'error_rate', incident_window.incident,
    )
    if err_series:
        peak_err = max(v for _, v in err_series)
        distress += peak_err

    status_series = query_api.get_metric_series(
        service, 'status', incident_window.incident,
    )
    if status_series:
        dead_count = sum(1 for _, v in status_series if v == 0)
        if dead_count > 0:
            distress += 0.5

    return min(distress, 2.0)


def score_accusations(
    blame_edges: list[BlameEdge],
    cascade_participants: list[str],
    scenario_config: dict,
    k8s_events: list[dict],
    incident_window: IncidentWindow,
    anomaly_summaries: dict[str, AnomalySummary] | None = None,
    query_api=None,
) -> list[CausalCandidate]:
    services_cfg = scenario_config.get('services', {})
    call_graph = scenario_config.get('call_graph', {})

    config_changes = _detect_config_changes(k8s_events, incident_window)

    blamed_by_map: dict[str, list[BlameEdge]] = defaultdict(list)
    for edge in blame_edges:
        blamed_by_map[edge.accused].append(edge)

    candidates = []
    for service in cascade_participants:
        has_cc = service in config_changes

        anomaly_score = 0.0
        error_count = 0
        if anomaly_summaries and service in anomaly_summaries:
            anomaly_score = anomaly_summaries[service].anomaly_score
            error_count = anomaly_summaries[service].error_count

        depth = _compute_depth(service, call_graph)

        metric_distress = 0.0
        if query_api and anomaly_score < 0.3:
            metric_distress = _get_metric_distress(
                query_api, service, incident_window,
            )

        effective_anomaly = max(anomaly_score, metric_distress * 0.8)

        suspicion = effective_anomaly

        suspicion += depth * 0.15

        if has_cc:
            suspicion *= (1 + ALPHA)

        if error_count == 0 and effective_anomaly < 0.1:
            suspicion *= 0.01

        blame_from_anomalous = 0.0
        for edge in blamed_by_map.get(service, []):
            accuser_anomaly = 0.0
            if anomaly_summaries and edge.accuser in anomaly_summaries:
                accuser_anomaly = anomaly_summaries[edge.accuser].anomaly_score
            blame_from_anomalous += edge.weight * edge.confidence * accuser_anomaly

        suspicion += blame_from_anomalous * 0.3

        candidates.append(CausalCandidate(
            service=service,
            suspicion_score=suspicion,
            blamed_by=blamed_by_map.get(service, []),
            has_config_change=has_cc,
            recovery_info=None,
            falsified=False,
            falsification_reason='',
        ))

    candidates.sort(key=lambda c: c.suspicion_score, reverse=True)
    return candidates
