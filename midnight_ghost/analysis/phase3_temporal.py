from __future__ import annotations

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.query.types import AnomalySummary, TimeRange
from midnight_ghost.analysis.types import CausalCandidate, IncidentWindow, RecoveryInfo

# Error rate must drop below 2x baseline to count as recovered
RECOVERY_ERROR_THRESHOLD = 2.0
# Recovery must hold for 30s to be considered real (not a flicker)
RECOVERY_SUSTAIN_SECONDS = 30
# Transition under 2s = step function (deploy/kill); over = gradual decay
STEP_TRANSITION_SECONDS = 2
# Throughput must stay above 70% to rule out "recovered by losing traffic"
THROUGHPUT_MAINTAIN_RATIO = 0.7


def _value_at(series: list[tuple[int, float]], target_ns: int) -> float:
    if not series:
        return 0.0
    best = series[0]
    best_dist = abs(series[0][0] - target_ns)
    for ts, val in series:
        dist = abs(ts - target_ns)
        if dist < best_dist:
            best = (ts, val)
            best_dist = dist
    return best[1]


def _peak_value(series: list[tuple[int, float]]) -> float:
    if not series:
        return 0.0
    return max(v for _, v in series)


def _last_value(series: list[tuple[int, float]]) -> float:
    if not series:
        return 0.0
    return series[-1][1]


def _find_recovery_point(
    error_series: list[tuple[int, float]],
    baseline_rate: float,
) -> int | None:
    threshold = max(baseline_rate * RECOVERY_ERROR_THRESHOLD, 0.05)
    for i, (ts, val) in enumerate(error_series):
        if val < threshold:
            sustain_end = ts + RECOVERY_SUSTAIN_SECONDS * 1_000_000_000
            stayed_low = all(
                v < threshold for t, v in error_series[i:]
                if t <= sustain_end
            )
            if stayed_low:
                return ts
    return None


def _transition_duration(
    error_series: list[tuple[int, float]],
    recovery_time: int,
) -> int:
    recovered_val = _value_at(error_series, recovery_time)
    threshold = recovered_val * 3
    high_point = recovery_time
    for ts, val in reversed(error_series):
        if ts < recovery_time and val > threshold:
            high_point = ts
            break
    return recovery_time - high_point


def _compute_baseline_error_rate(
    query_api: QueryAPI,
    service: str,
    baseline_window: TimeRange,
) -> float:
    series = query_api.get_metric_series(service, 'error_rate', baseline_window)
    if not series:
        return 0.0
    vals = [v for _, v in series]
    return sum(vals) / len(vals) if vals else 0.0


def analyze_recovery(
    query_api: QueryAPI,
    candidates: list[CausalCandidate],
    incident_window: IncidentWindow,
    anomaly_summaries: dict[str, AnomalySummary],
) -> list[CausalCandidate]:
    post_window = TimeRange(
        incident_window.alert_time_ns,
        incident_window.incident.end_ns,
    )

    for candidate in candidates:
        svc = candidate.service

        error_series = query_api.get_metric_series(svc, 'error_rate', post_window)
        throughput_series = query_api.get_metric_series(svc, 'request_rate', post_window)

        if not error_series:
            candidate.recovery_info = RecoveryInfo(
                service=svc,
                recovery_time_ns=None,
                recovery_type='none',
                trajectory='none',
                error_rate_before=0,
                error_rate_after=0,
                throughput_before=0,
                throughput_after=0,
            )
            continue

        baseline_err_rate = _compute_baseline_error_rate(
            query_api, svc, incident_window.baseline,
        )

        recovery_time = _find_recovery_point(error_series, baseline_err_rate)

        if recovery_time is None:
            candidate.recovery_info = RecoveryInfo(
                service=svc,
                recovery_time_ns=None,
                recovery_type='none',
                trajectory='none',
                error_rate_before=_peak_value(error_series),
                error_rate_after=_last_value(error_series),
                throughput_before=0,
                throughput_after=0,
            )
            continue

        err_before = _value_at(error_series, recovery_time - 5_000_000_000)
        err_after = _value_at(error_series, recovery_time + 10_000_000_000)
        thru_before = _value_at(throughput_series, recovery_time - 5_000_000_000) if throughput_series else 0
        thru_after = _value_at(throughput_series, recovery_time + 10_000_000_000) if throughput_series else 0

        thru_maintained = (
            thru_after > (thru_before * THROUGHPUT_MAINTAIN_RATIO)
            if thru_before > 0 else True
        )

        if err_after < err_before * 0.3 and thru_maintained:
            recovery_type = 'real'
        elif err_after < err_before * 0.3 and not thru_maintained:
            recovery_type = 'apparent'
        else:
            recovery_type = 'none'

        transition_dur = _transition_duration(error_series, recovery_time)
        if transition_dur < STEP_TRANSITION_SECONDS * 1_000_000_000:
            trajectory = 'step'
        else:
            trajectory = 'decay'

        candidate.recovery_info = RecoveryInfo(
            service=svc,
            recovery_time_ns=recovery_time,
            recovery_type=recovery_type,
            trajectory=trajectory,
            error_rate_before=err_before,
            error_rate_after=err_after,
            throughput_before=thru_before,
            throughput_after=thru_after,
        )

    recovery_order = [
        c for c in candidates
        if c.recovery_info and c.recovery_info.recovery_type == 'real'
    ]
    recovery_order.sort(key=lambda c: c.recovery_info.recovery_time_ns)

    return recovery_order
