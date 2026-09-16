from __future__ import annotations

import re
from collections import defaultdict

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.analysis.types import BlameEdge, IncidentWindow, Phase05Result


def _extract_service_name(template_text: str, known_services: set[str]) -> str | None:
    for svc in known_services:
        if re.search(rf'\b{re.escape(svc)}\b', template_text, re.IGNORECASE):
            return svc
    return None


def _extract_trace_blame(
    query_api: QueryAPI,
    incident_window: IncidentWindow,
    participant_set: set[str],
) -> list[BlameEdge]:
    """Extract blame edges directly from trace spans.

    For each error span whose parent is from a different service:
      accuser = parent service (the caller that experienced the failure)
      accused = span service (the callee that failed)
      weight  = count of such error spans (accumulated per pair)
    """
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)

    for trace_id, spans in query_api._trace_index.items():
        span_map = {s['span_id']: s for s in spans}
        for span in spans:
            if span['status'] != 'ERROR':
                continue
            ts = span['timestamp_ns']
            if ts < incident_window.incident.start_ns or ts > incident_window.incident.end_ns:
                continue
            parent_id = span['parent_span_id']
            if not parent_id:
                continue
            parent = span_map.get(parent_id)
            if not parent or parent['service'] == span['service']:
                continue
            accuser = parent['service']
            accused = span['service']
            if accuser in participant_set and accused in participant_set:
                pair_counts[(accuser, accused)] += 1

    edges = []
    for (accuser, accused), count in pair_counts.items():
        edges.append(BlameEdge(
            accuser=accuser,
            accused=accused,
            weight=float(count),
            source='trace',
            confidence=0.9,
        ))
    return edges


def _extract_log_blame(
    query_api: QueryAPI,
    cascade_participants: list[str],
    incident_window: IncidentWindow,
    known_services: set[str],
    participant_set: set[str],
) -> list[BlameEdge]:
    """Extract blame from log templates that name other services."""
    edges = []
    for service in cascade_participants:
        spikes = query_api.get_template_spikes(service, incident_window.incident)
        for spike in spikes:
            named = _extract_service_name(spike.template_text, known_services)
            if named and named != service and named in participant_set:
                edges.append(BlameEdge(
                    accuser=service,
                    accused=named,
                    weight=1.0,
                    source='log_content',
                    confidence=0.7,
                ))
    return edges


def extract_blame_edges(
    query_api: QueryAPI,
    cascade_participants: list[str],
    incident_window: IncidentWindow,
    known_services: set[str],
    call_graph: dict[str, list[str]] | None = None,
) -> list[BlameEdge]:
    participant_set = set(cascade_participants)

    trace_edges = _extract_trace_blame(query_api, incident_window, participant_set)

    log_edges = _extract_log_blame(
        query_api, cascade_participants, incident_window,
        known_services, participant_set,
    )

    return trace_edges + log_edges


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
