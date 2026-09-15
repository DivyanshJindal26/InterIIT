from __future__ import annotations

import json
import random
from typing import TextIO

from simulator.models import SpanData


def span_to_otel(span: SpanData) -> dict:
    attributes = [
        {"key": "http.method", "value": {"stringValue": span.http_method}},
        {"key": "http.status_code", "value": {"intValue": str(span.http_status_code)}},
        {"key": "http.url", "value": {"stringValue": span.http_url}},
    ]
    for k, v in span.attributes.items():
        if isinstance(v, int):
            attributes.append({"key": k, "value": {"intValue": str(v)}})
        elif isinstance(v, float):
            attributes.append({"key": k, "value": {"doubleValue": v}})
        else:
            attributes.append({"key": k, "value": {"stringValue": str(v)}})

    status = {"code": span.status_code}
    if span.status_message:
        status["message"] = span.status_message

    otel_span = {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "name": f"{span.http_method} {span.http_url}",
        "kind": "SPAN_KIND_SERVER",
        "startTimeUnixNano": str(span.start_time_ns),
        "endTimeUnixNano": str(span.end_time_ns),
        "status": status,
        "attributes": attributes,
    }
    if span.parent_span_id:
        otel_span["parentSpanId"] = span.parent_span_id
    else:
        otel_span["parentSpanId"] = ""

    return {
        "resourceSpans": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": span.service_name}},
                {"key": "k8s.pod.name", "value": {"stringValue": span.pod_name}},
            ]},
            "scopeSpans": [{"spans": [otel_span]}],
        }],
    }


def write_traces(
    spans: list[SpanData],
    error_trace_ids: set[str],
    rng: random.Random,
    out_file: TextIO,
    healthy_sample_rate: float = 0.05,
) -> None:
    traces: dict[str, list[SpanData]] = {}
    for span in spans:
        traces.setdefault(span.trace_id, []).append(span)

    for trace_id, trace_spans in traces.items():
        if trace_id in error_trace_ids:
            keep = True
        else:
            keep = rng.random() < healthy_sample_rate

        if keep:
            for span in trace_spans:
                out_file.write(json.dumps(span_to_otel(span), separators=(",", ":")) + "\n")
