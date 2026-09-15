from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


TICK_MS = 100

ZONE_OFFSETS_MS = {"us-east-1a": 0, "us-east-1b": 147, "us-east-1c": -83}
CLOCK_JITTER_STDDEV_MS = 5.0


class ServiceStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILING = "FAILING"
    DEAD = "DEAD"


class EdgeType(Enum):
    SYNC_RPC = "SYNC_RPC"
    ASYNC_QUEUE = "ASYNC_QUEUE"
    PERIODIC_POLL = "PERIODIC_POLL"


@dataclass
class ServiceConfig:
    name: str
    language: str
    log_framework: str
    zone: str
    base_latency_ms: float
    max_connections: int
    dependencies: list[str]
    pod_name: str
    edge_types: dict[str, str] = field(default_factory=dict)
    replica_count: int = 1
    ip_address: str = "10.0.0.1"


@dataclass
class ServiceState:
    cpu: float = 0.1
    memory: float = 0.2
    connections: int = 0
    error_rate: float = 0.0
    latency_multiplier: float = 1.0
    status: ServiceStatus = field(default_factory=lambda: ServiceStatus.HEALTHY)
    config_changed: bool = False
    restart_count: int = 0
    current_version: str = "v2.3.1"
    memory_leak_rate: float = 0.0
    fault_error_boost: float = 0.0
    fault_latency_multiplier: float = 1.0
    dead_since_ms: int = -1
    revived_at_ms: int = -1
    outbound_pool: int = 0
    outbound_pool_current: int = 0
    failing_ticks: int = 0


@dataclass
class FaultEvent:
    time_ms: int
    target: str
    fault_type: str
    params: dict = field(default_factory=dict)


@dataclass
class SpanData:
    trace_id: str
    span_id: str
    parent_span_id: str
    service_name: str
    pod_name: str
    operation: str
    start_time_ns: int
    end_time_ns: int
    status_code: str
    status_message: str
    http_method: str
    http_status_code: int
    http_url: str
    is_error: bool
    attributes: dict = field(default_factory=dict)


@dataclass
class MetricPoint:
    timestamp_ms: int
    service: str
    metric: str
    value: float
    labels: dict = field(default_factory=dict)


@dataclass
class K8sEvent:
    timestamp_ms: int
    service: str
    pod_name: str
    event_type: str
    reason: str
    message: str


@dataclass
class ScenarioConfig:
    name: str
    duration_ms: int
    seed: int
    request_rate: float
    faults: list[FaultEvent] = field(default_factory=list)
    description: str = ""


SERVICE_DEFAULTS: list[ServiceConfig] = [
    ServiceConfig(
        name="gateway", language="python", log_framework="stdlib",
        zone="us-east-1a", base_latency_ms=5.0, max_connections=2000,
        dependencies=["payments", "auth"],
        pod_name="gateway-a3f21", ip_address="10.0.0.1",
        edge_types={"payments": "SYNC_RPC", "auth": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="payments", language="java", log_framework="log4j",
        zone="us-east-1b", base_latency_ms=15.0, max_connections=500,
        dependencies=["redis", "db"],
        pod_name="payments-6d8f9", ip_address="10.0.0.2",
        edge_types={"redis": "SYNC_RPC", "db": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="auth", language="go", log_framework="zerolog",
        zone="us-east-1a", base_latency_ms=8.0, max_connections=1000,
        dependencies=["redis", "user-store"],
        pod_name="auth-b7e42", ip_address="10.0.0.3",
        edge_types={"redis": "SYNC_RPC", "user-store": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="redis", language="c", log_framework="redis-native",
        zone="us-east-1c", base_latency_ms=1.0, max_connections=10000,
        dependencies=[],
        pod_name="redis-c1d53", ip_address="10.0.0.5",
    ),
    ServiceConfig(
        name="db", language="c", log_framework="postgres-native",
        zone="us-east-1b", base_latency_ms=3.0, max_connections=200,
        dependencies=[],
        pod_name="db-d4e64", ip_address="10.0.0.6",
    ),
    ServiceConfig(
        name="user-store", language="go", log_framework="zerolog",
        zone="us-east-1c", base_latency_ms=6.0, max_connections=500,
        dependencies=[],
        pod_name="user-store-e5f75", ip_address="10.0.0.7",
    ),
    ServiceConfig(
        name="logging-svc", language="go", log_framework="zerolog",
        zone="us-east-1a", base_latency_ms=4.0, max_connections=300,
        dependencies=[],
        pod_name="logging-svc-f6a86", ip_address="10.0.0.8",
        edge_types={},
    ),
    ServiceConfig(
        name="monitoring", language="python", log_framework="stdlib",
        zone="us-east-1b", base_latency_ms=3.0, max_connections=500,
        dependencies=[],
        pod_name="monitoring-97b97", ip_address="10.0.0.9",
        edge_types={},
    ),
]

REQUEST_PATHS = [
    ["gateway", "payments", "redis"],
    ["gateway", "payments", "db"],
    ["gateway", "auth", "redis"],
    ["gateway", "auth", "user-store"],
]

HTTP_ENDPOINTS = {
    "gateway": [("POST", "/api/v1/charge"), ("GET", "/api/v1/status"), ("POST", "/api/v1/auth/login")],
    "payments": [("POST", "/charge"), ("POST", "/refund"), ("GET", "/balance")],
    "auth": [("POST", "/verify"), ("POST", "/login"), ("GET", "/token/refresh")],
    "redis": [("GET", "/GET"), ("SET", "/SET")],
    "db": [("SELECT", "/query"), ("INSERT", "/insert")],
    "user-store": [("GET", "/user/lookup"), ("GET", "/user/profile")],
    "logging-svc": [("POST", "/ingest"), ("GET", "/health")],
    "monitoring": [("GET", "/scrape"), ("GET", "/health")],
}
