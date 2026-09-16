# Midnight Ghost Simulator

Discrete event simulator for microservice cascade failures. Generates format-realistic telemetry (OTel traces, application logs, Prometheus metrics, K8s events) from physics-based cascade dynamics across 8 interconnected services.

Built for training and evaluating autonomous RCA (Root Cause Analysis) agents.

## Quick Start

```bash
# Run the default scenario
python -m simulator

# Run a specific scenario
python -m simulator deployment_cascade

# List all 25 scenarios
python -m simulator --list

# Custom output directory and seed
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

## Constraints

- Python 3.10+, no external dependencies
- Deterministic: same seed produces identical output
- Base scenarios run under 1 second each
- All cascade behavior emerges from physics, not scripts

## Fault Types

| Type | Effect |
|------|--------|
| `config_change` | Sets outbound connection pool size (floods downstream) |
| `rollback` | Reverts config, clears all fault state |
| `memory_leak` | Increases memory by N MB/sec until OOM |
| `latency_spike` | Multiplies base latency by N |
| `error_injection` | Adds N% error rate to service |
| `kill` | Immediately kills service (restarts after 3s) |
