from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.query.types import AnomalySummary, TimeRange
from midnight_ghost.analysis.types import IncidentWindow, Phase0Result


ALERT_THRESHOLD = 0.10
ANOMALY_THRESHOLD = 0.3


def _minute_key(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M')


def _minute_to_ns(minute_str: str) -> int:
    dt = datetime.strptime(minute_str, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1e9)


def detect_incident_window(
    query_api: QueryAPI,
    services: list[str],
    full_window: TimeRange,
) -> IncidentWindow:
    alert_time_ns = None

    for minute_str in query_api._all_minutes:
        minute_ns = _minute_to_ns(minute_str)
        if minute_ns < full_window.start_ns or minute_ns > full_window.end_ns:
            continue

        minute_window = TimeRange(minute_ns, minute_ns + 59_999_999_999)
        total_errors = 0
        total_events = 0

        for service in services:
            histo = query_api._get_severity_histo(service, minute_window)
            total_errors += histo.get('ERROR', 0) + histo.get('FATAL', 0)
            total_events += sum(histo.values())

        if total_events > 0 and (total_errors / total_events) > ALERT_THRESHOLD:
            alert_time_ns = minute_ns
            break

    if alert_time_ns is None:
        mid = full_window.start_ns + (full_window.end_ns - full_window.start_ns) // 2
        alert_time_ns = mid

    baseline_end = alert_time_ns - 5 * 60 * 1_000_000_000
    baseline_start = full_window.start_ns

    return IncidentWindow(
        baseline=TimeRange(baseline_start, max(baseline_start, baseline_end)),
        incident=TimeRange(alert_time_ns, full_window.end_ns),
        alert_time_ns=alert_time_ns,
    )


def identify_anomalous_services(
    query_api: QueryAPI,
    services: list[str],
    incident_window: IncidentWindow,
) -> list[tuple[str, AnomalySummary]]:
    anomalous = []
    for service in services:
        summary = query_api.get_anomaly_summary(service, incident_window.incident)
        if summary.anomaly_score > ANOMALY_THRESHOLD:
            anomalous.append((service, summary))
    return anomalous


def filter_by_trace_connectivity(
    query_api: QueryAPI,
    anomalous_services: list[tuple[str, AnomalySummary]],
    primary_symptom: str,
    incident_window: IncidentWindow,
) -> list[tuple[str, AnomalySummary]]:
    connected = set()
    connected.add(primary_symptom)

    for trace_id, spans in query_api._trace_index.items():
        has_primary = any(s['service'] == primary_symptom for s in spans)
        if has_primary:
            in_window = any(
                incident_window.incident.start_ns <= s['timestamp_ns'] <= incident_window.incident.end_ns
                for s in spans
            )
            if in_window:
                for s in spans:
                    connected.add(s['service'])

    return [(svc, summary) for svc, summary in anomalous_services if svc in connected]


def _get_dependency_services(
    anomalous_services: list[str],
    call_graph: dict[str, list[str]],
) -> set[str]:
    deps = set()
    for svc in anomalous_services:
        for dep in call_graph.get(svc, []):
            deps.add(dep)
    return deps


def run_phase0(
    query_api: QueryAPI,
    services: list[str],
    full_window: TimeRange,
    call_graph: dict[str, list[str]] | None = None,
) -> Phase0Result:
    incident_window = detect_incident_window(query_api, services, full_window)

    anomalous = identify_anomalous_services(query_api, services, incident_window)

    if not anomalous:
        primary_symptom = services[0] if services else ''
        return Phase0Result(
            incident_window=incident_window,
            cascade_participants=services,
            anomaly_summaries={},
            primary_symptom=primary_symptom,
        )

    trace_services = set()
    for spans in query_api._trace_index.values():
        for s in spans:
            trace_services.add(s['service'])

    traced_anomalous = [(svc, s) for svc, s in anomalous if svc in trace_services]
    if traced_anomalous:
        primary_symptom = max(traced_anomalous, key=lambda x: x[1].error_count)[0]
    else:
        primary_symptom = max(anomalous, key=lambda x: x[1].error_count)[0]

    filtered = filter_by_trace_connectivity(
        query_api, anomalous, primary_symptom, incident_window,
    )

    if not filtered:
        filtered = anomalous

    cascade_participants = [svc for svc, _ in filtered]
    anomaly_summaries = {svc: summary for svc, summary in filtered}

    if primary_symptom not in cascade_participants:
        cascade_participants.insert(0, primary_symptom)
        for svc, summary in anomalous:
            if svc == primary_symptom:
                anomaly_summaries[primary_symptom] = summary
                break

    if call_graph:
        dep_services = _get_dependency_services(cascade_participants, call_graph)
        for dep in dep_services:
            if dep not in cascade_participants and dep in services:
                cascade_participants.append(dep)
                summary = query_api.get_anomaly_summary(dep, incident_window.incident)
                anomaly_summaries[dep] = summary

    return Phase0Result(
        incident_window=incident_window,
        cascade_participants=cascade_participants,
        anomaly_summaries=anomaly_summaries,
        primary_symptom=primary_symptom,
    )
