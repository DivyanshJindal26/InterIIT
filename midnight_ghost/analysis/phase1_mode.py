from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.analysis.types import BlameEdge, IncidentWindow, Phase1Result

# 5x the average error rate in the first minute = spike
SPIKE_THRESHOLD = 5.0
# Second-half growth > 50% over first-half = escalation
SLOPE_THRESHOLD = 0.5


def _minute_key(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M')


def detect_failure_mode(
    query_api: QueryAPI,
    blame_edges: list[BlameEdge],
    cascade_participants: list[str],
    incident_window: IncidentWindow,
    has_config_change_services: set[str],
) -> Phase1Result:
    blame_per_minute: dict[str, float] = defaultdict(float)

    for service in cascade_participants:
        ts_cache = query_api._template_ts_cache.get(service, [])
        if not ts_cache:
            path_key = service
            if path_key not in query_api._template_ts_cache:
                query_api.get_template_spikes(service, incident_window.incident)
                ts_cache = query_api._template_ts_cache.get(service, [])

        incident_start_min = _minute_key(incident_window.incident.start_ns)
        incident_end_min = _minute_key(incident_window.incident.end_ns)

        for row in ts_cache:
            if incident_start_min <= row['minute'] <= incident_end_min:
                blame_per_minute[row['minute']] += row['count']

    sorted_minutes = sorted(blame_per_minute.keys())

    initial_spike = 0.0
    sustained_slope = 0.0

    if len(sorted_minutes) >= 2:
        first_val = blame_per_minute[sorted_minutes[0]]
        second_val = blame_per_minute[sorted_minutes[1]]
        avg_val = sum(blame_per_minute.values()) / len(sorted_minutes)
        initial_spike = first_val / max(avg_val, 0.1)

        if len(sorted_minutes) >= 3:
            mid = len(sorted_minutes) // 2
            first_half = [blame_per_minute[m] for m in sorted_minutes[:mid]]
            second_half = [blame_per_minute[m] for m in sorted_minutes[mid:]]
            avg_first = sum(first_half) / max(len(first_half), 1)
            avg_second = sum(second_half) / max(len(second_half), 1)
            sustained_slope = (avg_second - avg_first) / max(avg_first, 0.1)
    elif len(sorted_minutes) == 1:
        initial_spike = SPIKE_THRESHOLD + 1

    has_any_config_change = bool(has_config_change_services)

    if initial_spike > SPIKE_THRESHOLD and sustained_slope < SLOPE_THRESHOLD:
        mode = 'deployment' if has_any_config_change else 'external'
    elif initial_spike < SPIKE_THRESHOLD and sustained_slope > SLOPE_THRESHOLD:
        mode = 'exhaustion'
    elif initial_spike > SPIKE_THRESHOLD and sustained_slope > SLOPE_THRESHOLD:
        mode = 'drift'
    else:
        mode = 'unknown'

    return Phase1Result(
        failure_mode=mode,
        initial_spike=initial_spike,
        sustained_slope=sustained_slope,
    )
