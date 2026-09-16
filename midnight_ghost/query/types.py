from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TimeRange:
    start_ns: int
    end_ns: int

    def __str__(self) -> str:
        return f'[{self.start_ns}, {self.end_ns}]'


@dataclass
class AnomalySummary:
    service: str
    window: TimeRange
    anomaly_score: float
    error_count: int
    top_templates: list[str]


@dataclass
class TemplateSpike:
    template_id: str
    template_text: str
    normal_rate: float
    spike_rate: float
    spike_factor: float
    tags: list[str]


@dataclass
class SpanNode:
    span_id: str
    parent_span_id: str
    service: str
    operation: str
    duration_ms: float
    status: str
    timestamp_ns: int
