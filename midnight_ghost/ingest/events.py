from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict


@dataclass
class Event:
    event_id: str
    timestamp_ns: int
    service: str
    zone: str
    severity: str
    source_type: str
    format_type: str
    template_id: str
    template_text: str
    params: dict
    tags: list[str]
    trace_id: str
    span_id: str
    parent_span_id: str
    raw: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(',', ':'))

    @staticmethod
    def from_json(line: str) -> Event:
        d = json.loads(line)
        return Event(**d)


def make_event_id() -> str:
    return uuid.uuid4().hex[:24]


def build_log_event(
    parsed_line,
    zone: str,
    template_id: str,
    template_text: str,
    params: dict,
    tags: list[str],
) -> Event:
    return Event(
        event_id=make_event_id(),
        timestamp_ns=parsed_line.timestamp_ns,
        service=parsed_line.service,
        zone=zone,
        severity=parsed_line.severity,
        source_type='log',
        format_type=parsed_line.format_type,
        template_id=template_id,
        template_text=template_text,
        params=params,
        tags=tags,
        trace_id=parsed_line.trace_id or '',
        span_id=parsed_line.span_id or '',
        parent_span_id='',
        raw=parsed_line.raw,
    )


def build_trace_event(
    span_data: dict,
    zone: str,
    tags: list[str],
) -> Event:
    resource = span_data['resourceSpans'][0]['resource']
    svc_name = ''
    pod_name = ''
    for attr in resource['attributes']:
        if attr['key'] == 'service.name':
            svc_name = attr['value']['stringValue']
        elif attr['key'] == 'k8s.pod.name':
            pod_name = attr['value']['stringValue']

    span = span_data['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
    trace_id = span['traceId']
    span_id = span['spanId']
    parent_span_id = span.get('parentSpanId', '')
    operation = span['name']
    start_ns = int(span['startTimeUnixNano'])
    end_ns = int(span['endTimeUnixNano'])
    duration_ms = (end_ns - start_ns) / 1_000_000
    status_code = span['status']['code']
    status_msg = span['status'].get('message', '')

    severity = 'ERROR' if 'ERROR' in status_code else 'INFO'

    http_status = 200
    for attr in span.get('attributes', []):
        if attr['key'] == 'http.status_code':
            http_status = int(attr['value'].get('intValue', '200'))

    return Event(
        event_id=make_event_id(),
        timestamp_ns=start_ns,
        service=svc_name,
        zone=zone,
        severity=severity,
        source_type='trace',
        format_type='OTEL_SPAN',
        template_id=operation,
        template_text=operation,
        params={'duration_ms': round(duration_ms, 2), 'http_status': http_status,
                'status_message': status_msg},
        tags=tags,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        raw=json.dumps(span_data, separators=(',', ':')),
    )


def build_metric_event(
    metric_data: dict,
    zone: str,
    base_time_ns: int,
) -> Event:
    ts_ms = metric_data['timestamp_ms']
    ts_ns = base_time_ns + ts_ms * 1_000_000
    service = metric_data['service']
    metric_name = metric_data['metric']
    value = metric_data['value']
    labels = metric_data.get('labels', {})

    return Event(
        event_id=make_event_id(),
        timestamp_ns=ts_ns,
        service=service,
        zone=zone,
        severity='INFO',
        source_type='metric',
        format_type='METRIC',
        template_id=metric_name,
        template_text=metric_name,
        params={'value': value, 'labels': labels},
        tags=[],
        trace_id='',
        span_id='',
        parent_span_id='',
        raw=json.dumps(metric_data, separators=(',', ':')),
    )


def build_k8s_event(
    event_data: dict,
    zone: str,
    base_time_ns: int,
) -> Event:
    ts_ms = event_data['timestamp_ms']
    ts_ns = base_time_ns + ts_ms * 1_000_000
    service = event_data['service']
    reason = event_data['reason']
    message = event_data['message']

    tags = []
    if 'OOMKill' in reason:
        tags.append('pod_eviction')
    if 'CrashLoopBackOff' in reason:
        tags.append('crash_loop')
    if 'Restart' in reason:
        tags.append('pod_restart')
    if 'Deploy' in reason or 'Rollback' in reason:
        tags.append('deployment')

    return Event(
        event_id=make_event_id(),
        timestamp_ns=ts_ns,
        service=service,
        zone=zone,
        severity='WARN',
        source_type='k8s_event',
        format_type='K8S_EVENT',
        template_id=reason,
        template_text=message,
        params={'reason': reason, 'pod_name': event_data.get('pod_name', ''),
                'event_type': event_data.get('event_type', '')},
        tags=tags,
        trace_id='',
        span_id='',
        parent_span_id='',
        raw=json.dumps(event_data, separators=(',', ':')),
    )
