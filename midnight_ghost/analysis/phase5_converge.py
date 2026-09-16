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

    # Sort by suspicion score
    ranking = sorted(surviving, key=lambda c: c.suspicion_score, reverse=True)
    top = ranking[0]
    runner_up = ranking[1] if len(ranking) > 1 else None

    # If top candidate is clearly ahead, use it directly
    if runner_up is None or top.suspicion_score > runner_up.suspicion_score * 1.2:
        top_service = top.service
        confidence = 0.9
    else:
        # Close race — use recovery ordering as tiebreaker
        # Only consider services whose suspicion is close to the top
        contenders = [c for c in ranking
                      if c.suspicion_score >= top.suspicion_score * 0.7]

        recovery_winner = None
        contender_names = {c.service for c in contenders}
        for c in contenders:
            if (c.recovery_info
                    and c.recovery_info.recovery_type == 'real'
                    and c.recovery_info.trajectory == 'step'):
                recovery_winner = c.service
                break

        if recovery_winner:
            top_service = recovery_winner
            confidence = 0.7
        else:
            top_service = top.service
            confidence = 0.5

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
        'accusation_top': ranking[0].service,
        'recovery_tiebreaker': None,
        'selected': top_service,
        'suspicion_scores': {c.service: c.suspicion_score for c in ranking},
    }

    return root_cause, signal_agreement
