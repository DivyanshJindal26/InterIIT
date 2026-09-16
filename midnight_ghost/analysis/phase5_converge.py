from __future__ import annotations

from collections import defaultdict

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.analysis.types import (
    CausalCandidate,
    IncidentWindow,
    RootCause,
)


def _get_top_template_id(
    query_api: QueryAPI,
    service: str,
    incident_window: IncidentWindow,
) -> str:
    spikes = query_api.get_template_spikes(service, incident_window.incident)
    if spikes:
        return spikes[0].template_id
    summary = query_api.get_anomaly_summary(service, incident_window.incident)
    if summary.top_templates:
        return summary.top_templates[0]
    return ''


def _build_propagation_path(
    root_cause_svc: str,
    candidates: list[CausalCandidate],
    recovery_order: list[CausalCandidate],
) -> list[str]:
    path = [root_cause_svc]
    if recovery_order:
        for c in reversed(recovery_order):
            if c.service != root_cause_svc and c.service not in path:
                path.append(c.service)
    else:
        for c in candidates:
            if c.service != root_cause_svc and c.service not in path:
                path.append(c.service)
    return path


def converge(
    query_api: QueryAPI,
    candidates: list[CausalCandidate],
    recovery_order: list[CausalCandidate],
    signal_weights: dict[str, float],
    incident_window: IncidentWindow,
    failure_mode: str,
) -> tuple[RootCause, dict]:
    surviving = [c for c in candidates if not c.falsified]
    if not surviving:
        surviving = list(candidates)
        if surviving:
            surviving[0].falsified = False

    if not surviving:
        return RootCause(
            service='unknown',
            confidence=0.0,
            failure_mode=failure_mode,
            evidence=[],
            propagation_path=[],
            onset_time_ns=None,
        ), {}

    accusation_ranking = sorted(surviving, key=lambda c: c.suspicion_score, reverse=True)
    accusation_top = accusation_ranking[0].service

    step_recoveries = [
        c for c in surviving
        if c.recovery_info and c.recovery_info.trajectory == 'step'
    ]
    recovery_top = None
    if step_recoveries:
        recovery_top = step_recoveries[0].service
    elif recovery_order:
        surviving_names = {c.service for c in surviving}
        for c in recovery_order:
            if c.service in surviving_names:
                recovery_top = c.service
                break

    wavefront_top = None

    accusation_top_candidate = accusation_ranking[0] if accusation_ranking else None
    accusation_no_recovery = (
        accusation_top_candidate is not None
        and (accusation_top_candidate.recovery_info is None
             or accusation_top_candidate.recovery_info.recovery_type == 'none')
    )
    recovery_weight_adj = signal_weights.get('recovery', 0.6)
    accusation_weight_adj = signal_weights.get('accusation', 0.7)
    if accusation_no_recovery and recovery_top and recovery_top != accusation_top:
        recovery_weight_adj *= 0.1
        accusation_weight_adj = max(accusation_weight_adj, 0.8)

    signal_scores: dict[str, float] = defaultdict(float)

    for c in surviving:
        svc = c.service
        max_sus = accusation_ranking[0].suspicion_score if accusation_ranking else 1.0
        if max_sus > 0:
            signal_scores[svc] += accusation_weight_adj * (c.suspicion_score / max_sus)

        if recovery_top and recovery_top == svc:
            signal_scores[svc] += recovery_weight_adj * 1.0
        elif c.recovery_info and c.recovery_info.trajectory == 'step':
            signal_scores[svc] += recovery_weight_adj * 0.8

        if wavefront_top and wavefront_top == svc:
            signal_scores[svc] += signal_weights.get('wavefront', 0.6) * 1.0

    groups = [g for g in [accusation_top, recovery_top, wavefront_top] if g is not None]
    if not groups:
        top_service = surviving[0].service
        confidence = 0.3
    else:
        top_service = max(signal_scores, key=signal_scores.get)
        agreeing = sum(1 for g in groups if g == top_service)
        total = len(groups)
        if agreeing == total and total >= 2:
            confidence = 0.9
        elif agreeing >= 2:
            confidence = 0.7
        elif agreeing == 1:
            confidence = 0.5
        else:
            confidence = 0.3

    top_candidate = next((c for c in surviving if c.service == top_service), surviving[0])
    evidence: list[str] = []
    template_id = _get_top_template_id(query_api, top_service, incident_window)
    if template_id:
        examples = query_api.get_examples(
            template_id=template_id,
            service=top_service,
            n=5,
        )
        evidence = [e.raw for e in examples]

    propagation = _build_propagation_path(top_service, candidates, recovery_order)

    onset_time = incident_window.alert_time_ns

    root_cause = RootCause(
        service=top_service,
        confidence=confidence,
        failure_mode=failure_mode,
        evidence=evidence,
        propagation_path=propagation,
        onset_time_ns=onset_time,
    )

    signal_agreement = {
        'group_a_accusation': accusation_top,
        'group_b_recovery': recovery_top,
        'group_c_wavefront': wavefront_top,
        'signal_scores': dict(signal_scores),
    }

    return root_cause, signal_agreement
