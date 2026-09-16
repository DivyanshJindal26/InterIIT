from __future__ import annotations

from collections import defaultdict

from midnight_ghost.analysis.types import (
    BlameEdge,
    CausalCandidate,
    IncidentWindow,
)
from midnight_ghost.query.types import AnomalySummary, TimeRange

# Design doc: config change makes a service 2.5x more suspicious
ALPHA = 1.5
# Anomaly score floor for a service to register as distressed
ANOMALY_FLOOR = 0.3
# Net blame contribution to suspicion (blame is a modifier, not the primary signal)
BLAME_WEIGHT = 0.5
# OOMKilled services get at least this much added suspicion
KILL_BOOST = 0.8

CONFIG_CHANGE_REASONS = {
    'Deployed', 'Started', 'ConfigChanged', 'Rolled',
    'RollingUpdate', 'ScalingReplicaSet',
    'DeploymentUpdated', 'RollbackTriggered',
}

KILL_REASONS = {
    'OOMKilled', 'CrashLoopBackOff', 'Killed', 'Evicted',
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


def _detect_killed_services(
    k8s_events: list[dict],
    incident_window: IncidentWindow,
) -> set[str]:
    killed: set[str] = set()
    for event in k8s_events:
        reason = event.get('reason', '')
        if reason in KILL_REASONS:
            ts = event.get('timestamp_ns', 0)
            if incident_window.incident.start_ns <= ts <= incident_window.incident.end_ns:
                svc = event.get('service', '')
                if svc:
                    killed.add(svc)
    return killed


def _get_metric_distress(
    query_api,
    service: str,
    incident_window: IncidentWindow,
) -> float:
    # Captures silent killers: services causing latency/status problems
    # without producing ERROR log entries (e.g., db with 200x latency).
    distress = 0.0

    lat_series = query_api.get_metric_series(
        service, 'latency_multiplier', incident_window.incident,
    )
    if lat_series:
        peak_lat = max(v for _, v in lat_series)
        if peak_lat > 5:
            distress += min(1.5, peak_lat / 50.0)

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
    call_graph = scenario_config.get('call_graph', {})

    config_changes = _detect_config_changes(k8s_events, incident_window)
    killed_services = _detect_killed_services(k8s_events, incident_window)

    # Aggregate blame edges per accused and per accuser
    inbound_blame: dict[str, float] = defaultdict(float)
    outbound_blame: dict[str, float] = defaultdict(float)
    blamed_by_map: dict[str, list[BlameEdge]] = defaultdict(list)

    for edge in blame_edges:
        inbound_blame[edge.accused] += edge.weight
        outbound_blame[edge.accuser] += edge.weight
        blamed_by_map[edge.accused].append(edge)

    # Normalize blame so it's comparable across scenarios
    max_blame = max(
        max(inbound_blame.values(), default=0),
        max(outbound_blame.values(), default=0),
        1.0,
    )

    metric_distress_map: dict[str, float] = {}
    if query_api:
        for service in cascade_participants:
            a = 0.0
            if anomaly_summaries and service in anomaly_summaries:
                a = anomaly_summaries[service].anomaly_score
            if a < ANOMALY_FLOOR:
                metric_distress_map[service] = _get_metric_distress(
                    query_api, service, incident_window,
                )

    candidates = []
    for service in cascade_participants:
        has_cc = service in config_changes
        was_killed = service in killed_services

        # Primary signal: anomaly score from log error ratio
        anomaly_score = 0.0
        error_count = 0
        if anomaly_summaries and service in anomaly_summaries:
            anomaly_score = anomaly_summaries[service].anomaly_score
            error_count = anomaly_summaries[service].error_count

        metric_distress = metric_distress_map.get(service, 0.0)
        effective_anomaly = max(anomaly_score, metric_distress * 0.8)

        ib = inbound_blame.get(service, 0) / max_blame
        ob = outbound_blame.get(service, 0) / max_blame
        net_blame = ib - ob

        suspicion = effective_anomaly + net_blame * BLAME_WEIGHT

        if has_cc:
            suspicion *= (1 + ALPHA)

        if was_killed:
            suspicion = max(suspicion, effective_anomaly + KILL_BOOST)

        # Dependency distress: a service with high metric distress but no
        # log anomaly, whose caller IS anomalous, is a silent root cause
        if metric_distress > ANOMALY_FLOOR and anomaly_score < 0.1:
            for caller, callees in call_graph.items():
                if service in callees:
                    caller_anomaly = 0.0
                    if anomaly_summaries and caller in anomaly_summaries:
                        caller_anomaly = anomaly_summaries[caller].anomaly_score
                    if caller_anomaly > ANOMALY_FLOOR:
                        suspicion += metric_distress * BLAME_WEIGHT
                        break

        # Entry-point origin: no callers means the fault can't have been
        # inherited from upstream — outbound blame = it caused downstream
        # errors, not that it suffered from dependencies
        has_callers = any(service in callees for callees in call_graph.values())
        if (not has_callers
                and effective_anomaly > ANOMALY_FLOOR
                and ob > ANOMALY_FLOOR
                and ib == 0):
            suspicion += ob

        if error_count == 0 and effective_anomaly < 0.1 and not was_killed:
            suspicion *= 0.01

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
