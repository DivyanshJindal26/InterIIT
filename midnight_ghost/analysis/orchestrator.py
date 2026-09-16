from __future__ import annotations

import json
import os

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.query.types import TimeRange
from midnight_ghost.analysis.types import IncidentReport, IncidentWindow
from midnight_ghost.analysis.phase0_isolation import run_phase0
from midnight_ghost.analysis.phase05_blame import run_phase05
from midnight_ghost.analysis.phase1_mode import detect_failure_mode
from midnight_ghost.analysis.phase2_accusation import score_accusations
from midnight_ghost.analysis.phase3_temporal import analyze_recovery
from midnight_ghost.analysis.phase4_falsification import falsify_hypotheses
from midnight_ghost.analysis.phase5_converge import converge
from midnight_ghost.analysis.phase6_remediation import plan_remediation, validate_remediation


def _load_k8s_events(output_path: str, base_time_ns: int) -> list[dict]:
    k8s_path = os.path.join(output_path, 'k8s_events', 'events.jsonl')
    if not os.path.exists(k8s_path):
        return []
    events = []
    with open(k8s_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            ts_ms = event.get('timestamp_ms', 0)
            event['timestamp_ns'] = base_time_ns + ts_ms * 1_000_000
            events.append(event)
    return events


def _build_call_graph_deps(scenario_config: dict) -> dict:
    call_graph = scenario_config.get('call_graph', {})
    services = scenario_config.get('services', {})
    dep_graph = {}
    for svc, svc_cfg in services.items():
        deps = svc_cfg.get('dependencies', [])
        if not deps:
            deps = call_graph.get(svc, [])
        dep_graph[svc] = {'dependencies': deps}
    return dep_graph


def run_rca(
    query_api: QueryAPI,
    scenario_config: dict,
    output_path: str,
) -> IncidentReport:
    services = list(scenario_config['services'].keys())

    bounds = query_api.get_time_bounds()
    if bounds is None:
        full_window = TimeRange(0, 120_000_000_000)
    else:
        full_window = bounds

    base_time_ns = full_window.start_ns
    k8s_events = _load_k8s_events(output_path, base_time_ns)
    dependency_graph = _build_call_graph_deps(scenario_config)

    call_graph = scenario_config.get('call_graph', {})

    phase0 = run_phase0(query_api, services, full_window, call_graph)

    phase05 = run_phase05(
        query_api,
        phase0.cascade_participants,
        phase0.incident_window,
        services,
        call_graph,
    )

    config_change_services = set()
    for event in k8s_events:
        reason = event.get('reason', '')
        if reason in ('Deployed', 'Started', 'ConfigChanged', 'Rolled',
                       'RollingUpdate', 'ScalingReplicaSet'):
            ts = event.get('timestamp_ns', 0)
            pre_start = phase0.incident_window.incident.start_ns - 10 * 60 * 1_000_000_000
            pre_end = phase0.incident_window.incident.start_ns
            if pre_start <= ts <= pre_end:
                config_change_services.add(event.get('service', ''))

    phase1 = detect_failure_mode(
        query_api,
        phase05.blame_edges,
        phase0.cascade_participants,
        phase0.incident_window,
        config_change_services,
    )

    candidates = score_accusations(
        phase05.blame_edges,
        phase0.cascade_participants,
        scenario_config,
        k8s_events,
        phase0.incident_window,
        phase0.anomaly_summaries,
        query_api,
    )

    recovery_order = analyze_recovery(
        query_api,
        candidates,
        phase0.incident_window,
        phase0.anomaly_summaries,
    )

    falsify_hypotheses(candidates, recovery_order, dependency_graph)

    root_cause, signal_agreement = converge(
        query_api,
        candidates,
        recovery_order,
        phase0.incident_window,
        phase1.failure_mode,
    )

    remediation = plan_remediation(root_cause, dependency_graph)

    validation = validate_remediation(
        query_api,
        root_cause,
        remediation,
        phase0.incident_window,
        phase0.cascade_participants,
    )

    budget_report = query_api.get_budget_report()

    return IncidentReport(
        root_cause=root_cause,
        candidates=candidates,
        remediation=remediation,
        validation=validation,
        failure_mode=phase1.failure_mode,
        incident_window=phase0.incident_window,
        budget_used=budget_report['budget_used'],
        budget_log=budget_report['queries'],
        signal_agreement=signal_agreement,
    )
