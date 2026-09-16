from __future__ import annotations

import re

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.analysis.types import BlameEdge, IncidentWindow, Phase05Result


def _extract_service_name(template_text: str, known_services: set[str]) -> str | None:
    for svc in known_services:
        if re.search(rf'\b{re.escape(svc)}\b', template_text, re.IGNORECASE):
            return svc
    return None


def extract_blame_edges(
    query_api: QueryAPI,
    cascade_participants: list[str],
    incident_window: IncidentWindow,
    known_services: set[str],
    call_graph: dict[str, list[str]] | None = None,
) -> list[BlameEdge]:
    edges: list[BlameEdge] = []
    participant_set = set(cascade_participants)

    for service in cascade_participants:
        # Trace parentage: who called this service?
        # If gateway calls payments and payments is failing, gateway accuses payments.
        # get_trace_parents returns the *callers* of `service`.
        # Each caller is a potential accuser — they accuse `service` (the dependency).
        parents = query_api.get_trace_parents(service, incident_window.incident)
        for parent_svc, proportion in parents.items():
            if parent_svc in participant_set:
                edges.append(BlameEdge(
                    accuser=parent_svc,
                    accused=service,
                    weight=proportion,
                    source='trace',
                    confidence=0.9,
                ))

        # Log content: does the template name another service?
        spikes = query_api.get_template_spikes(service, incident_window.incident)
        for spike in spikes:
            named = _extract_service_name(spike.template_text, known_services)
            if named and named != service and named in participant_set:
                edges.append(BlameEdge(
                    accuser=service,
                    accused=named,
                    weight=spike.spike_factor,
                    source='log_content',
                    confidence=0.7,
                ))

    # Dependency-based blame: if a service has upstream_failure/timeout tags
    # AND its dependency is anomalous or went dark, add a blame edge.
    if call_graph:
        for service in cascade_participants:
            deps = call_graph.get(service, [])
            spikes_fetched = False
            has_upstream_tags = False
            for dep in deps:
                if dep not in participant_set or dep == service:
                    continue
                dep_summary = query_api.get_anomaly_summary(dep, incident_window.incident)
                dep_total = sum(query_api._get_severity_histo(dep, incident_window.incident).values())
                dep_is_suspect = dep_summary.anomaly_score > 0.2 or dep_total < 5
                if not dep_is_suspect:
                    continue
                if not spikes_fetched:
                    spikes = query_api.get_template_spikes(service, incident_window.incident)
                    for spike in spikes:
                        if any(t in spike.tags for t in ('upstream_failure', 'timeout')):
                            has_upstream_tags = True
                            break
                    spikes_fetched = True
                if has_upstream_tags:
                    edges.append(BlameEdge(
                        accuser=service,
                        accused=dep,
                        weight=1.0,
                        source='dependency_error',
                        confidence=0.8,
                    ))

    return edges


def run_phase05(
    query_api: QueryAPI,
    cascade_participants: list[str],
    incident_window: IncidentWindow,
    all_services: list[str],
    call_graph: dict[str, list[str]] | None = None,
) -> Phase05Result:
    blame_edges = extract_blame_edges(
        query_api, cascade_participants, incident_window,
        set(all_services), call_graph,
    )
    return Phase05Result(blame_edges=blame_edges)
