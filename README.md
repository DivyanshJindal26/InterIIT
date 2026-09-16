# Midnight Ghost

Autonomous RCA (Root Cause Analysis) system for microservice cascade failures. Includes a physics-based simulator that generates format-realistic telemetry, and a production-grade ingestion pipeline that builds a queryable, indexed event store from the raw output.

## Quick Start

```bash
# 1. Generate telemetry
python -m simulator deployment_cascade

# 2. Ingest and index
pip install drain3 pybloom_live
python -m midnight_ghost.ingest \
    --input output/deployment_cascade/ \
    --output event_store/deployment_cascade/ \
    --index-output indexes/deployment_cascade/

# 3. Query (from Python)
from midnight_ghost.query.api import QueryAPI
from midnight_ghost.query.types import TimeRange

api = QueryAPI('event_store/deployment_cascade/', 'indexes/deployment_cascade/')
bounds = api.get_time_bounds()
summary = api.get_anomaly_summary('payments', bounds)
spikes = api.get_template_spikes('payments', bounds)
```

### Simulator Only

```bash
python -m simulator deployment_cascade
python -m simulator --list          # List all 25 scenarios
python -m simulator needle_in_haystack -o my_output --seed 12345
```

## Architecture

```
gateway (Python/stdlib) ──SYNC_RPC──> payments (Java/Log4j) ──SYNC_RPC──> redis (C/redis-native)
       │                                      │
       │                                      └──SYNC_RPC──> db (C/postgres-native)
       │
       └──SYNC_RPC──> auth (Go/zerolog) ──SYNC_RPC──> redis
                             │
                             └──SYNC_RPC──> user-store (Go/zerolog)

logging-svc (Go/zerolog)    # background flapper
monitoring  (Python/stdlib) # metrics scraper
```

Each service is a state machine with independent CPU, memory, connections, error rate, and latency. Faults are injected at specific times; cascade behavior emerges from physics rules, not scripts.

## Physics Rules

The simulation engine applies these rules every tick (100ms):

1. **Connection pressure** - high connection ratio drives CPU up
2. **Connection exhaustion** - at max connections, error rate spikes
3. **CPU pressure** - CPU > 90% causes latency explosion
4. **Dependency latency** - slow downstream causes caller timeout errors
5. **Dependency failure** - failing/dead downstream propagates errors to callers
6. **Thundering herd** - failing services retry into dependencies (with backoff)
7. **Memory pressure** - memory > 95% triggers OOM kill
8. **Java GC** - Java services get GC pauses when memory > 80%
9. **Natural recovery** - connections drain, CPU/error rate decay over time

## Output Structure

```
output/<scenario_name>/
  traces/traces.jsonl          # OTel spans (tail-sampled: 100% errors, 5% healthy)
  logs/<service>.log           # Format-realistic logs per language
  metrics/metrics.jsonl        # Prometheus-style time series
  k8s_events/events.jsonl      # Pod lifecycle events
  ground_truth.json            # Injected faults (for RCA evaluation)
  scenario_config.json         # Full scenario configuration
```

### Log Formats

Each service emits logs matching its real-world framework:

| Service | Language | Format |
|---------|----------|--------|
| gateway | Python | nginx access log + stdlib ERROR |
| payments | Java | Log4j + HikariCP pool exhaustion |
| auth | Go | zerolog JSON |
| redis | C | redis-native |
| db | C | postgres-native |
| user-store | Go | zerolog JSON |
| logging-svc | Go | zerolog JSON |
| monitoring | Python | stdlib |

### Traces

OpenTelemetry format with proper span hierarchy (parent-child across services). Tail-based sampling keeps 100% of traces containing any error span and 5% of healthy traces.

### Clock Skew

Per-zone offsets simulate real distributed clock drift:

| Zone | Offset |
|------|--------|
| us-east-1a | 0ms |
| us-east-1b | +147ms |
| us-east-1c | -83ms |

Plus Gaussian jitter (stddev 5ms) on every timestamp.

## Scenarios

### Basic (single root cause)

| Scenario | Description |
|----------|-------------|
| `deployment_cascade` | Bad payments deploy (pool size 500) causes cascade, rollback at 70s |
| `memory_leak` | Auth memory leak leads to OOM and gateway cascade |
| `db_latency_spike` | Database goes 200x slow, payments cascade |
| `redis_failure` | Redis killed, affecting auth and payments |
| `traffic_surge` | 10x traffic spike overwhelms all services |
| `user_store_latency` | User-store slow, only auth path degrades |
| `auth_error_spike` | Auth errors, only auth-dependent paths fail |
| `gateway_overload` | Bad gateway deploy, errors at entry point |

### Compound (multiple interacting faults)

| Scenario | Description |
|----------|-------------|
| `multi_fault` | Redis latency + auth memory leak simultaneously |
| `rolling_restart_failure` | Auth and payments killed in overlapping windows |
| `cascading_kill` | Redis -> payments domino failure |
| `payments_memory_leak` | Java GC storms + connection pool flood |
| `db_connection_flood` | Payments overflows db connection limit |
| `double_deploy_conflict` | Simultaneous bad deploys to payments and auth |
| `slow_burn_cascade` | Gradually escalating db latency (5x -> 20x -> 100x -> 300x) |
| `total_meltdown` | Three faults across db, redis, and payments |
| `intermittent_errors` | Flapping error injection on payments |

### Red Herring (noise faults obscure root cause)

| Scenario | Real Root Cause | Red Herrings |
|----------|----------------|--------------|
| `needle_in_haystack` | DB latency 200x | Auth leak, user-store errors, redis kill |
| `ghost_in_the_machine` | Payments pool flood + redis latency | Auth errors, user-store latency, db kill |
| `blame_the_wrong_service` | Redis death (causes db overload via retries) | Auth errors, user-store latency |
| `redis_latency_auth_kill` | Redis latency (causes auth OOM) | Auth appears to be the problem |
| `chaos_monkey` | Staggered kills across 4 services | DB latency, user-store errors, gateway latency |
| `the_perfect_storm` | Payments pool + db latency | Auth leak, redis errors, user-store kill, gateway latency |
| `false_recovery` | Wave 1: payments errors; Wave 2: db latency | Auth latency, redis errors, user-store kill |
| `whack_a_mole` | 4 sequential: redis -> payments -> db -> auth leak | Auth latency, user-store errors, gateway latency |

## Scale Mode

Generate massive output for stress testing RCA systems against production-scale data volumes.

```bash
# ~500MB: 10x traffic, padded logs, 3 replicas
python -m simulator deployment_cascade --scale 10 --pad-logs --replicas 3

# ~50GB: high traffic, all features
python -m simulator the_perfect_storm --scale 100 --pad-logs --replicas 5 --duration 600000

# ~500GB: extreme scale with streaming (constant memory)
python -m simulator total_meltdown \
  --scale 1000 --pad-logs --replicas 10 \
  --duration 3600000 --streaming
```

### Scale Flags

| Flag | Effect |
|------|--------|
| `--scale N` | Multiply request rate by N (default: 1.0) |
| `--duration MS` | Override scenario duration in milliseconds |
| `--pad-logs` | Add stack traces, HTTP headers, request bodies to every log line |
| `--replicas N` | Simulate N pod replicas per service (N log files each) |
| `--streaming` | Flush to disk each tick instead of buffering in memory |

### What `--pad-logs` Adds

- HTTP headers (X-Request-Id, Authorization, X-B3-TraceId, Envoy metadata)
- Request bodies (transaction payloads, user lookups, SQL queries)
- Stack traces on errors (Java: full exception chains; Go: goroutine dumps; Python: tracebacks)
- Envoy sidecar metadata (upstream_cluster, response_flags, service_time)

Each log line grows from ~200 bytes to ~2-5KB, realistic for production services.

## Ingestion Pipeline

Takes raw simulator output and builds a queryable, indexed event store. Designed for streaming — processes 500 GB without holding all events in memory.

```bash
python -m midnight_ghost.ingest \
    --input output/deployment_cascade/ \
    --output event_store/deployment_cascade/ \
    --index-output indexes/deployment_cascade/
```

### Pipeline Stages

| Stage | Module | What it does |
|-------|--------|-------------|
| 1. Format Detection | `parsers.py` | Auto-detects log format (zerolog, log4j, nginx, redis, postgres, python stdlib) and extracts standard fields |
| 2. Template Extraction | `drain_wrapper.py` | Drain3 clusters log messages into templates with parameter extraction |
| 3. Semantic Tagging | `tagger.py` | Regex rules map templates to failure categories (resource_exhaustion, timeout, upstream_failure, etc.) |
| 4. Event Construction | `events.py` | Unified Event dataclass for logs, traces, metrics, and k8s events |
| 5. Partitioned Store | `store.py` | Writes events to `service/minute.jsonl` files with bounded file handle pool |
| 6. Index Construction | `indexer.py` | Builds template time series, severity histograms, trace index, metric series, bloom filters |
| 7. Query API | `query/api.py` | Budget-tracked query interface over the indexed event store |

### Event Store Layout

```
event_store/<scenario>/
├── gateway/
│   ├── 2024-01-15T14:29.jsonl
│   ├── 2024-01-15T14:30.jsonl
│   └── ...
├── payments/
│   └── ...
└── ...
```

### Index Layout

```
indexes/<scenario>/
├── template_ts/<service>.jsonl      # Template counts per minute
├── severity/<service>.jsonl         # Severity histogram per minute
├── traces.jsonl                     # Trace ID -> span list
├── metrics/<service>/<metric>.jsonl # Time series per metric
├── bloom/<service>/<minute>.bloom   # Token bloom filters
└── template_texts.json              # Template ID -> template text
```

## Query API

Budget-tracked interface the RCA agent calls. Each method costs 1 budget unit.

| Method | Returns | Description |
|--------|---------|-------------|
| `get_anomaly_summary(service, window)` | `AnomalySummary` | Error ratio vs baseline, anomaly score 0-1, top templates |
| `get_template_spikes(service, window)` | `list[TemplateSpike]` | Templates that spiked vs baseline (>3x), with failure tags |
| `get_trace_parents(service, window)` | `dict[str, float]` | Which services were calling this one (parent proportions) |
| `get_trace_spans(trace_id)` | `list[SpanNode]` | All spans for a trace with parent-child linkage |
| `get_metric_series(service, metric, window)` | `list[tuple]` | Time series `[(timestamp_ns, value), ...]` |
| `get_examples(template_id, service, n)` | `list[Event]` | N example events matching a template (reservoir sampled) |
| `bloom_check(service, window, token)` | `bool` | Does this partition contain this token? O(1) |
| `get_budget_report()` | `dict` | Total queries used + full query log |

```python
from midnight_ghost.query.api import QueryAPI
from midnight_ghost.query.types import TimeRange

api = QueryAPI('event_store/scenario/', 'indexes/scenario/')
bounds = api.get_time_bounds()

# Which services are anomalous?
for svc in api.list_services():
    s = api.get_anomaly_summary(svc, bounds)
    if s.error_count > 0:
        print(f'{svc}: score={s.anomaly_score:.2f} errors={s.error_count}')

# What error templates spiked?
spikes = api.get_template_spikes('payments', bounds)
for s in spikes:
    print(f'[{s.spike_factor:.0f}x] {s.template_text}  tags={s.tags}')

# Who was calling this service?
parents = api.get_trace_parents('redis', bounds)
# → {'auth': 0.67, 'payments': 0.33}
```

## Project Structure

```
midnight_ghost/
├── simulator/              # Telemetry generator
│   ├── engine.py           # Simulation engine (physics rules, tick loop)
│   ├── scenarios.py        # 25 fault scenarios
│   ├── logs.py             # Format-realistic log emitters
│   ├── traces.py           # OTel span serialization
│   └── models.py           # Service configs, state, data models
├── ingest/                 # Ingestion pipeline
│   ├── __main__.py         # CLI entry point
│   ├── parsers.py          # Format detection + per-format parsers
│   ├── drain_wrapper.py    # Drain3 template extraction
│   ├── tagger.py           # Semantic tagging rules
│   ├── events.py           # Event dataclass + builders
│   ├── store.py            # Partitioned event store writer
│   └── indexer.py          # Index construction + bloom filters
├── query/                  # Query layer
│   ├── api.py              # QueryAPI class
│   └── types.py            # TimeRange, AnomalySummary, TemplateSpike, SpanNode
└── analysis/               # RCA agent (coming next)
```

## Requirements

- Python 3.10+
- Simulator: no external dependencies
- Ingestion pipeline: `drain3`, `pybloom_live`

## Constraints

- Deterministic: same seed produces identical output
- Base scenarios run under 1 second each
- All cascade behavior emerges from physics, not scripts
- Ingestion handles 500 GB without running out of memory
- Each query API call returns in < 1 second on indexed data

## Fault Types

| Type | Effect |
|------|--------|
| `config_change` | Sets outbound connection pool size (floods downstream) |
| `rollback` | Reverts config, clears all fault state |
| `memory_leak` | Increases memory by N MB/sec until OOM |
| `latency_spike` | Multiplies base latency by N |
| `error_injection` | Adds N% error rate to service |
| `kill` | Immediately kills service (restarts after 3s) |
