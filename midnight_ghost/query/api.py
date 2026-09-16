from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

from midnight_ghost.ingest.events import Event
from midnight_ghost.ingest.indexer import SimpleBloomFilter
from midnight_ghost.ingest.tagger import compute_tags
from midnight_ghost.query.types import (
    AnomalySummary,
    SpanNode,
    TemplateSpike,
    TimeRange,
)


def _minute_key(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M')


def _minute_range(window: TimeRange) -> list[str]:
    start_min = _minute_key(window.start_ns)
    end_min = _minute_key(window.end_ns)
    minutes = []
    current = start_min
    while current <= end_min:
        minutes.append(current)
        dt = datetime.strptime(current, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        dt = dt.replace(minute=dt.minute + 1) if dt.minute < 59 else dt.replace(hour=dt.hour + 1, minute=0)
        current = dt.strftime('%Y-%m-%dT%H:%M')
        if len(minutes) > 10000:
            break
    return minutes


class QueryAPI:
    def __init__(self, event_store_path: str, index_path: str) -> None:
        self.event_store_path = event_store_path
        self.index_path = index_path
        self.budget_used = 0
        self.query_log: list[dict] = []

        self._trace_index: dict[str, list[dict]] = {}
        self._severity_cache: dict[str, list[dict]] = {}
        self._template_ts_cache: dict[str, list[dict]] = {}
        self._template_texts: dict[str, str] = {}
        self._all_minutes: list[str] = []

        self._load_trace_index()
        self._load_template_texts()
        self._scan_minutes()

    def _load_trace_index(self) -> None:
        path = os.path.join(self.index_path, 'traces.jsonl')
        if not os.path.exists(path):
            return
        with open(path) as f:
            for line in f:
                data = json.loads(line)
                self._trace_index[data['trace_id']] = data['spans']

    def _load_template_texts(self) -> None:
        path = os.path.join(self.index_path, 'template_texts.json')
        if os.path.exists(path):
            with open(path) as f:
                self._template_texts = json.load(f)

    def _scan_minutes(self) -> None:
        minutes_set = set()
        sev_dir = os.path.join(self.index_path, 'severity')
        if os.path.exists(sev_dir):
            for fname in os.listdir(sev_dir):
                if fname.endswith('.jsonl'):
                    with open(os.path.join(sev_dir, fname)) as f:
                        for line in f:
                            data = json.loads(line)
                            minutes_set.add(data['minute'])
        self._all_minutes = sorted(minutes_set)

    def _get_severity_histo(self, service: str, window: TimeRange) -> dict[str, int]:
        if service not in self._severity_cache:
            path = os.path.join(self.index_path, 'severity', f'{service}.jsonl')
            if not os.path.exists(path):
                self._severity_cache[service] = []
            else:
                rows = []
                with open(path) as f:
                    for line in f:
                        rows.append(json.loads(line))
                self._severity_cache[service] = rows

        totals: dict[str, int] = defaultdict(int)
        start_min = _minute_key(window.start_ns)
        end_min = _minute_key(window.end_ns)
        for row in self._severity_cache[service]:
            if start_min <= row['minute'] <= end_min:
                for k, v in row.items():
                    if k != 'minute' and isinstance(v, int):
                        totals[k] += v
        return dict(totals)

    def _get_template_rates(self, service: str, window: TimeRange) -> dict[str, float]:
        if service not in self._template_ts_cache:
            path = os.path.join(self.index_path, 'template_ts', f'{service}.jsonl')
            if not os.path.exists(path):
                self._template_ts_cache[service] = []
            else:
                rows = []
                with open(path) as f:
                    for line in f:
                        rows.append(json.loads(line))
                self._template_ts_cache[service] = rows

        start_min = _minute_key(window.start_ns)
        end_min = _minute_key(window.end_ns)
        counts: dict[str, int] = defaultdict(int)
        n_minutes = 0
        seen = set()
        for row in self._template_ts_cache[service]:
            if start_min <= row['minute'] <= end_min:
                counts[row['template_id']] += row['count']
                if row['minute'] not in seen:
                    seen.add(row['minute'])
                    n_minutes += 1
        n_minutes = max(n_minutes, 1)
        return {tid: count / n_minutes for tid, count in counts.items()}

    def _baseline_window(self) -> TimeRange:
        if not self._all_minutes:
            return TimeRange(0, 0)
        n = max(1, len(self._all_minutes) // 4)
        start = self._all_minutes[0]
        end = self._all_minutes[n - 1]
        start_dt = datetime.strptime(start, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        return TimeRange(
            start_ns=int(start_dt.timestamp() * 1e9),
            end_ns=int(end_dt.timestamp() * 1e9) + 59_999_999_999,
        )

    def _get_baseline_error_ratio(self, service: str) -> float:
        bw = self._baseline_window()
        histo = self._get_severity_histo(service, bw)
        errors = histo.get('ERROR', 0) + histo.get('FATAL', 0)
        total = sum(histo.values())
        return errors / max(total, 1)

    def _get_top_templates(self, service: str, window: TimeRange, n: int = 5) -> list[str]:
        rates = self._get_template_rates(service, window)
        sorted_tids = sorted(rates.items(), key=lambda x: x[1], reverse=True)
        return [tid for tid, _ in sorted_tids[:n]]

    def _in_window(self, ts_ns: int, window: TimeRange) -> bool:
        return window.start_ns <= ts_ns <= window.end_ns

    def _find_span_service(self, spans: list[dict], span_id: str) -> str | None:
        for s in spans:
            if s['span_id'] == span_id:
                return s['service']
        return None

    # -- Public query methods (each costs 1 budget unit) --

    def get_anomaly_summary(self, service: str, window: TimeRange) -> AnomalySummary:
        self._charge('get_anomaly_summary', service=service, window=str(window))
        histo = self._get_severity_histo(service, window)
        error_count = histo.get('ERROR', 0) + histo.get('FATAL', 0)
        total = sum(histo.values())
        error_ratio = error_count / max(total, 1)
        baseline_ratio = self._get_baseline_error_ratio(service)
        anomaly_score = min(1.0, error_ratio / max(baseline_ratio, 0.01))
        top_templates = self._get_top_templates(service, window, n=5)
        return AnomalySummary(
            service=service,
            window=window,
            anomaly_score=anomaly_score,
            error_count=error_count,
            top_templates=top_templates,
        )

    def get_template_spikes(self, service: str, window: TimeRange) -> list[TemplateSpike]:
        self._charge('get_template_spikes', service=service, window=str(window))
        incident_rates = self._get_template_rates(service, window)
        baseline_rates = self._get_template_rates(service, self._baseline_window())
        spikes = []
        for tid, rate in incident_rates.items():
            base = baseline_rates.get(tid, 0)
            factor = rate / max(base, 0.1)
            if factor > 3.0:
                tmpl_text = self._template_texts.get(tid, tid)
                tags = compute_tags(tmpl_text)
                spikes.append(TemplateSpike(
                    template_id=tid, template_text=tmpl_text,
                    normal_rate=round(base, 2), spike_rate=round(rate, 2),
                    spike_factor=round(factor, 2), tags=tags,
                ))
        return sorted(spikes, key=lambda s: s.spike_factor, reverse=True)

    def get_trace_parents(self, service: str, window: TimeRange) -> dict[str, float]:
        self._charge('get_trace_parents', service=service, window=str(window))
        parent_counts: dict[str, int] = defaultdict(int)
        total = 0
        for trace_id, spans in self._trace_index.items():
            for span in spans:
                if span['service'] == service and self._in_window(span['timestamp_ns'], window):
                    parent_id = span['parent_span_id']
                    if parent_id:
                        parent_svc = self._find_span_service(spans, parent_id)
                        if parent_svc:
                            parent_counts[parent_svc] += 1
                            total += 1
        return {svc: round(count / max(total, 1), 4)
                for svc, count in parent_counts.items()}

    def get_trace_spans(self, trace_id: str) -> list[SpanNode]:
        self._charge('get_trace_spans', trace_id=trace_id)
        raw = self._trace_index.get(trace_id, [])
        return [SpanNode(
            span_id=s['span_id'],
            parent_span_id=s['parent_span_id'],
            service=s['service'],
            operation=s['operation'],
            duration_ms=s['duration_ms'],
            status=s['status'],
            timestamp_ns=s['timestamp_ns'],
        ) for s in raw]

    def get_metric_series(
        self, service: str, metric: str, window: TimeRange,
    ) -> list[tuple[int, float]]:
        self._charge('get_metric_series', service=service, metric=metric, window=str(window))
        path = os.path.join(self.index_path, 'metrics', service, f'{metric}.jsonl')
        if not os.path.exists(path):
            return []
        points = []
        with open(path) as f:
            for line in f:
                entry = json.loads(line)
                if self._in_window(entry['timestamp_ns'], window):
                    points.append((entry['timestamp_ns'], entry['value']))
        return points

    def get_examples(
        self, template_id: str, service: str, n: int = 5,
    ) -> list[Event]:
        self._charge('get_examples', template_id=template_id, service=service, n=n)
        import random
        reservoir: list[Event] = []
        count = 0
        svc_dir = os.path.join(self.event_store_path, service)
        if not os.path.exists(svc_dir):
            return []
        for fname in sorted(os.listdir(svc_dir)):
            if not fname.endswith('.jsonl'):
                continue
            with open(os.path.join(svc_dir, fname)) as f:
                for line in f:
                    event = Event.from_json(line.strip())
                    if event.template_id == template_id:
                        count += 1
                        if len(reservoir) < n:
                            reservoir.append(event)
                        else:
                            j = random.randint(0, count - 1)
                            if j < n:
                                reservoir[j] = event
        return reservoir

    def bloom_check(self, service: str, window: TimeRange, token: str) -> bool:
        self._charge('bloom_check', service=service, window=str(window), token=token)
        minutes = _minute_range(window)
        bloom_dir = os.path.join(self.index_path, 'bloom', service)
        if not os.path.exists(bloom_dir):
            return False
        for minute in minutes:
            path = os.path.join(bloom_dir, f'{minute}.bloom')
            if os.path.exists(path):
                bf = SimpleBloomFilter.load(path)
                if bf.check(token.lower()):
                    return True
        return False

    # -- Budget tracking --

    def _charge(self, method: str, **kwargs) -> None:
        self.budget_used += 1
        self.query_log.append({
            'query_number': self.budget_used,
            'method': method,
            'params': kwargs,
            'timestamp': time.time(),
        })

    def get_budget_report(self) -> dict:
        return {
            'budget_used': self.budget_used,
            'queries': self.query_log,
        }

    def list_services(self) -> list[str]:
        if not os.path.exists(self.event_store_path):
            return []
        return sorted(
            d for d in os.listdir(self.event_store_path)
            if os.path.isdir(os.path.join(self.event_store_path, d))
        )

    def list_metrics(self, service: str) -> list[str]:
        path = os.path.join(self.index_path, 'metrics', service)
        if not os.path.exists(path):
            return []
        return sorted(f[:-6] for f in os.listdir(path) if f.endswith('.jsonl'))

    def get_time_bounds(self) -> TimeRange | None:
        if not self._all_minutes:
            return None
        start = self._all_minutes[0]
        end = self._all_minutes[-1]
        s_dt = datetime.strptime(start, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        e_dt = datetime.strptime(end, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        return TimeRange(
            start_ns=int(s_dt.timestamp() * 1e9),
            end_ns=int(e_dt.timestamp() * 1e9) + 59_999_999_999,
        )
