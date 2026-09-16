from __future__ import annotations

import json
import os
import random
from copy import deepcopy

from simulator.logs import LogEmitter
from simulator.models import (
    CLOCK_JITTER_STDDEV_MS,
    REQUEST_PATHS,
    SERVICE_DEFAULTS,
    TICK_MS,
    ZONE_OFFSETS_MS,
    HTTP_ENDPOINTS,
    EdgeType,
    FaultEvent,
    K8sEvent,
    MetricPoint,
    ScenarioConfig,
    ServiceConfig,
    ServiceState,
    ServiceStatus,
    SpanData,
)
from simulator.traces import write_traces

DRAIN_RATE = 10
RETRY_CONNECTIONS = 8
DEAD_RESTART_TICKS = 30


class SimulationEngine:
    def __init__(
        self,
        scenario: ScenarioConfig,
        pad_logs: bool = False,
        replicas: int = 1,
        streaming: bool = False,
        output_dir: str | None = None,
    ):
        self.rng = random.Random(scenario.seed)
        self.scenario = scenario
        self.time_ms = 0
        self.tick_count = 0
        self.pad_logs = pad_logs
        self.replicas = max(1, replicas)
        self.streaming = streaming
        self.output_dir = output_dir

        self.configs: dict[str, ServiceConfig] = {}
        self.states: dict[str, ServiceState] = {}
        self._init_services()

        self.all_spans: list[SpanData] = []
        self.log_buffers: dict[str, list[str]] = {n: [] for n in self.configs}
        self.metrics_buffer: list[MetricPoint] = []
        self.k8s_events: list[K8sEvent] = []
        self.error_trace_ids: set[str] = set()

        self.log_emitter = LogEmitter(random.Random(self.rng.randint(0, 2**32)))

        self.pending_faults = sorted(scenario.faults, key=lambda f: f.time_ms)
        self.active_faults: list[FaultEvent] = []

        self._original_configs: dict[str, dict] = {}

        self._stream_files: dict[str, object] = {}
        if self.streaming and self.output_dir:
            self._init_streaming()

    def _init_services(self) -> None:
        source = self.scenario.services if self.scenario.services else SERVICE_DEFAULTS
        for svc in source:
            cfg = deepcopy(svc)
            self.configs[cfg.name] = cfg
            self.states[cfg.name] = ServiceState()

        self._request_paths = self.scenario.request_paths or REQUEST_PATHS
        self._http_endpoints = self.scenario.http_endpoints or HTTP_ENDPOINTS
        self._flapper_svc = self._find_flapper()

    def run(self) -> None:
        duration = self.scenario.duration_ms
        while self.time_ms < duration:
            self._tick()
            self.time_ms += TICK_MS
            self.tick_count += 1

    def _tick(self) -> None:
        self._process_faults()
        self._generate_requests()
        self._apply_physics()
        self._update_statuses()
        self._emit_metrics()
        self._generate_noise()
        if self.streaming:
            self._flush_streaming()

    def _wall_time(self, service_name: str) -> int:
        zone = self.configs[service_name].zone
        offset = ZONE_OFFSETS_MS.get(zone, 0)
        jitter = self.rng.gauss(0, CLOCK_JITTER_STDDEV_MS)
        base = 1705329000000 + self.time_ms
        return int(base + offset + jitter)

    def _gen_trace_id(self) -> str:
        return "".join(f"{self.rng.randint(0, 255):02x}" for _ in range(16))

    def _gen_span_id(self) -> str:
        return "".join(f"{self.rng.randint(0, 255):02x}" for _ in range(8))

    def _process_faults(self) -> None:
        while self.pending_faults and self.pending_faults[0].time_ms <= self.time_ms:
            fault = self.pending_faults.pop(0)
            self._apply_fault(fault)

    def _apply_fault(self, fault: FaultEvent) -> None:
        target = fault.target
        if target not in self.states:
            return
        state = self.states[target]
        config = self.configs[target]
        params = fault.params

        if fault.fault_type == "config_change":
            if target not in self._original_configs:
                self._original_configs[target] = {
                    "max_connections": config.max_connections,
                }
            if "connection_pool_size" in params:
                state.outbound_pool = params["connection_pool_size"]
                state.outbound_pool_current = 0
            state.config_changed = True
            wt = self._wall_time(target)
            version = params.get("version", "v2.4.0")
            state.current_version = version
            lines = self.log_emitter.emit_deployment_log(wt, config, version, "start")
            self.log_buffers[target].extend(lines)
            self.k8s_events.append(K8sEvent(
                timestamp_ms=self.time_ms, service=target,
                pod_name=config.pod_name, event_type="deployment_started",
                reason="DeploymentUpdated",
                message=f"Deployment {version} started",
            ))

        elif fault.fault_type == "rollback":
            if params.get("revert_config") and target in self._original_configs:
                orig = self._original_configs[target]
                config.max_connections = orig["max_connections"]
            state.config_changed = False
            state.fault_error_boost = 0.0
            state.fault_latency_multiplier = 1.0
            state.memory_leak_rate = 0.0
            state.outbound_pool = 0
            state.outbound_pool_current = 0
            old_version = state.current_version
            state.current_version = params.get("version", "v2.3.1")
            wt = self._wall_time(target)
            lines = self.log_emitter.emit_deployment_log(
                wt, config, state.current_version, "rollback",
            )
            self.log_buffers[target].extend(lines)
            self.k8s_events.append(K8sEvent(
                timestamp_ms=self.time_ms, service=target,
                pod_name=config.pod_name, event_type="deployment_rollback",
                reason="RollbackTriggered",
                message=f"Rolling back from {old_version} to {state.current_version}",
            ))

        elif fault.fault_type == "memory_leak":
            rate = params.get("leak_rate_mb_per_sec", 10)
            state.memory_leak_rate = rate / 1000.0 * (TICK_MS / 1000.0)

        elif fault.fault_type == "latency_spike":
            multiplier = params.get("multiplier", 10)
            state.fault_latency_multiplier = multiplier

        elif fault.fault_type == "error_injection":
            rate = params.get("error_rate", 0.5)
            state.fault_error_boost = rate

        elif fault.fault_type == "kill":
            state.status = ServiceStatus.DEAD
            state.dead_since_ms = self.time_ms
            wt = self._wall_time(target)
            lines = self.log_emitter.emit_k8s_oom_log(wt, config)
            self.log_buffers[target].extend(lines)
            self.k8s_events.append(K8sEvent(
                timestamp_ms=self.time_ms, service=target,
                pod_name=config.pod_name, event_type="pod_evicted",
                reason="OOMKilled",
                message=f"Pod {config.pod_name} evicted: OOMKilled",
            ))

        self.active_faults.append(fault)

    def _find_flapper(self) -> str | None:
        on_paths = set()
        for p in self._request_paths:
            on_paths.update(p)
        for name, cfg in self.configs.items():
            if name not in on_paths and not cfg.dependencies:
                return name
        return None

    def _generate_requests(self) -> None:
        n_requests = int(self.scenario.request_rate)
        frac = self.scenario.request_rate - n_requests
        if self.rng.random() < frac:
            n_requests += 1

        for _ in range(n_requests):
            path_idx = self.rng.randint(0, len(self._request_paths) - 1)
            path = self._request_paths[path_idx]
            trace_id = self._gen_trace_id()
            method, url = self.rng.choice(self._http_endpoints.get(path[0], [("GET", "/")]))
            self._route_request(path, trace_id, method, url)

    def _route_request(
        self,
        path: list[str],
        trace_id: str,
        method: str,
        url: str,
        parent_span_id: str = "",
    ) -> bool:
        has_error = False
        prev_span_id = parent_span_id

        for i, svc_name in enumerate(path):
            config = self.configs[svc_name]
            state = self.states[svc_name]

            span_id = self._gen_span_id()
            svc_method, svc_url = self.rng.choice(
                self._http_endpoints.get(svc_name, [("GET", "/")])
            )
            if i == 0:
                svc_method = method
                svc_url = url

            latency_ms = config.base_latency_ms * state.latency_multiplier
            latency_ms *= state.fault_latency_multiplier
            latency_ms += self.rng.gauss(0, max(1, latency_ms * 0.1))
            latency_ms = max(0.1, latency_ms)

            conn_weight = max(1, int(latency_ms / TICK_MS))
            state.connections += conn_weight

            if state.status == ServiceStatus.DEAD:
                status_code = 503
                error_msg = "service unavailable"
                is_error = True
            elif self.rng.random() < (state.error_rate + state.fault_error_boost):
                if state.connections >= config.max_connections:
                    status_code = 503
                    error_msg = "connection pool exhausted"
                else:
                    status_code = 500
                    error_msg = "internal server error"
                is_error = True
            else:
                status_code = 200
                error_msg = ""
                is_error = False

            wt = self._wall_time(svc_name)
            start_ns = wt * 1_000_000
            end_ns = start_ns + int(latency_ms * 1_000_000)

            span = SpanData(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=prev_span_id,
                service_name=svc_name,
                pod_name=config.pod_name,
                operation=f"{svc_method} {svc_url}",
                start_time_ns=start_ns,
                end_time_ns=end_ns,
                status_code="STATUS_CODE_ERROR" if is_error else "STATUS_CODE_OK",
                status_message=error_msg,
                http_method=svc_method,
                http_status_code=status_code,
                http_url=svc_url,
                is_error=is_error,
            )
            self.all_spans.append(span)

            if is_error:
                self.error_trace_ids.add(trace_id)
                has_error = True
                downstream = path[i + 1] if i + 1 < len(path) else ""
                log_lines = self.log_emitter.emit_request_log(
                    wt, config, state, svc_method, svc_url, status_code,
                    latency_ms, trace_id, error_msg, downstream,
                )
                self.log_buffers[svc_name].extend(log_lines)
                break

            if i == 0 and svc_name == "gateway":
                log_lines = self.log_emitter.emit_request_log(
                    wt, config, state, svc_method, svc_url, status_code,
                    latency_ms, trace_id,
                )
                self.log_buffers[svc_name].extend(log_lines)

            prev_span_id = span_id

        if has_error and len(path) > 1:
            failing_idx = min(
                len(path) - 1,
                next(
                    (j for j, s in enumerate(path)
                     if self.states[s].status in (ServiceStatus.FAILING, ServiceStatus.DEAD)),
                    len(path) - 1,
                ),
            )
            for j in range(failing_idx):
                caller = path[j]
                called = path[failing_idx]
                if caller == "gateway":
                    wt = self._wall_time(caller)
                    cfg = self.configs[caller]
                    st = self.states[caller]
                    lines = self.log_emitter.emit_request_log(
                        wt, cfg, st, method, url, 504,
                        cfg.base_latency_ms * st.latency_multiplier * 5,
                        trace_id,
                        f"upstream {called} failed",
                        called,
                    )
                    self.log_buffers[caller].extend(lines)

        return not has_error

    def _apply_physics(self) -> None:
        for name, state in self.states.items():
            config = self.configs[name]

            if state.status == ServiceStatus.DEAD:
                if (self.time_ms - state.dead_since_ms) > DEAD_RESTART_TICKS * TICK_MS:
                    state.status = ServiceStatus.HEALTHY
                    state.cpu = 0.1
                    state.memory = 0.2
                    state.connections = 0
                    state.error_rate = 0.0
                    state.latency_multiplier = 1.0
                    state.restart_count += 1
                    state.revived_at_ms = self.time_ms
                    self.k8s_events.append(K8sEvent(
                        timestamp_ms=self.time_ms, service=name,
                        pod_name=config.pod_name, event_type="pod_restarted",
                        reason="Restarted",
                        message=f"Pod {config.pod_name} restarted (count={state.restart_count})",
                    ))
                continue

            if state.memory_leak_rate > 0:
                state.memory = min(1.0, state.memory + state.memory_leak_rate)

            if state.outbound_pool > 0:
                ramp = max(1, state.outbound_pool // 10)
                state.outbound_pool_current = min(
                    state.outbound_pool,
                    state.outbound_pool_current + ramp,
                )
            elif state.outbound_pool_current > 0:
                state.outbound_pool_current = max(0, state.outbound_pool_current - 20)

            if state.outbound_pool_current > 0:
                for dep_name in config.dependencies:
                    dep_state = self.states.get(dep_name)
                    if dep_state and dep_state.status != ServiceStatus.DEAD:
                        dep_state.connections = max(
                            dep_state.connections, state.outbound_pool_current,
                        )

            conn_ratio = state.connections / max(1, config.max_connections)
            if conn_ratio > 0.9:
                state.cpu = min(1.0, state.cpu + 0.1 * (conn_ratio - 0.9) * 10)

            if state.connections >= config.max_connections:
                state.error_rate = min(1.0, state.error_rate + 0.15)

            if state.cpu > 0.9:
                state.latency_multiplier = 1 + (state.cpu - 0.9) * 50

            for dep_name in config.dependencies:
                dep_state = self.states.get(dep_name)
                if not dep_state:
                    continue
                dep_config = self.configs[dep_name]
                dep_latency_ms = (
                    dep_config.base_latency_ms
                    * dep_state.latency_multiplier
                    * dep_state.fault_latency_multiplier
                )
                if dep_latency_ms > 100:
                    state.error_rate = min(
                        1.0, state.error_rate + (dep_latency_ms - 100) / 500,
                    )
                    state.connections += int(dep_latency_ms / TICK_MS)

            for dep_name in config.dependencies:
                dep_state = self.states.get(dep_name)
                if dep_state and dep_state.status in (ServiceStatus.FAILING, ServiceStatus.DEAD):
                    if dep_state.status == ServiceStatus.DEAD:
                        dep_err = 1.0
                    else:
                        dep_err = dep_state.error_rate + dep_state.fault_error_boost
                    state.error_rate = min(1.0, state.error_rate + dep_err * 0.25)

            if state.status == ServiceStatus.FAILING:
                state.failing_ticks += 1
                backoff = max(0, RETRY_CONNECTIONS - state.failing_ticks // 5)
                if backoff > 0:
                    for dep_name in config.dependencies:
                        dep_state = self.states.get(dep_name)
                        if dep_state and dep_state.status != ServiceStatus.DEAD:
                            dep_state.connections += backoff
            else:
                state.failing_ticks = 0

            if state.memory > 0.95:
                state.status = ServiceStatus.DEAD
                state.dead_since_ms = self.time_ms
                wt = self._wall_time(name)
                lines = self.log_emitter.emit_k8s_oom_log(wt, config)
                self.log_buffers[name].extend(lines)
                self.k8s_events.append(K8sEvent(
                    timestamp_ms=self.time_ms, service=name,
                    pod_name=config.pod_name, event_type="pod_evicted",
                    reason="OOMKilled",
                    message=f"Pod {config.pod_name} evicted: OOMKilled, "
                            f"memory={state.memory:.2f}",
                ))
                continue

            if config.language == "java" and state.memory > 0.8:
                wt = self._wall_time(name)
                gc_lines = self.log_emitter.emit_gc_log(wt, config, state)
                self.log_buffers[name].extend(gc_lines)
                state.memory = max(0.3, state.memory * 0.85)
                state.cpu = min(1.0, state.cpu + 0.05)

            state.connections = max(0, state.connections - DRAIN_RATE)
            state.cpu = max(0.05, state.cpu * 0.92)
            state.error_rate = max(0, state.error_rate * 0.88)
            if state.cpu <= 0.9:
                state.latency_multiplier = max(1.0, state.latency_multiplier * 0.92)

    def _update_statuses(self) -> None:
        for name, state in self.states.items():
            if state.status == ServiceStatus.DEAD:
                continue
            total_err = state.error_rate + state.fault_error_boost
            if total_err >= 0.5 or state.cpu >= 0.95:
                state.status = ServiceStatus.FAILING
            elif total_err >= 0.1 or state.cpu >= 0.8:
                state.status = ServiceStatus.DEGRADED
            else:
                state.status = ServiceStatus.HEALTHY

    def _emit_metrics(self) -> None:
        if self.tick_count % 10 != 0:
            return
        for name, state in self.states.items():
            config = self.configs[name]
            ts = self.time_ms
            base_labels = {"service": name, "pod": config.pod_name}
            for metric, value in [
                ("cpu_usage", state.cpu),
                ("memory_usage", state.memory),
                ("connections_active", float(state.connections)),
                ("connections_max", float(config.max_connections)),
                ("error_rate", state.error_rate + state.fault_error_boost),
                ("latency_multiplier", state.latency_multiplier * state.fault_latency_multiplier),
                ("status", float(
                    {"HEALTHY": 0, "DEGRADED": 1, "FAILING": 2, "DEAD": 3}[state.status.value]
                )),
            ]:
                self.metrics_buffer.append(MetricPoint(
                    timestamp_ms=ts, service=name, metric=metric,
                    value=value, labels=base_labels,
                ))

    def _generate_noise(self) -> None:
        for name in self.configs:
            config = self.configs[name]
            state = self.states[name]
            if state.status == ServiceStatus.DEAD:
                continue
            wt = self._wall_time(name)

            if self.tick_count % 50 == 0:
                lines = self.log_emitter.emit_health_check_log(wt, config, state)
                self.log_buffers[name].extend(lines)

            if self.tick_count % 100 == 0 and config.max_connections > 0:
                lines = self.log_emitter.emit_pool_stats_log(wt, config, state)
                self.log_buffers[name].extend(lines)

            if config.log_framework == "postgres-native" and self.tick_count % 200 == 0:
                dur = self.rng.randint(100, 2000)
                lines = self.log_emitter.emit_slow_query_log(wt, config, dur)
                self.log_buffers[name].extend(lines)

            if config.log_framework == "redis-native" and self.tick_count % 150 == 0:
                lines = self.log_emitter.emit_redis_background_log(wt, config, state)
                self.log_buffers[name].extend(lines)

            if self.tick_count % 500 == 0 and self.rng.random() < 0.1:
                days = self.rng.randint(7, 30)
                lines = self.log_emitter.emit_tls_warning(wt, config, days)
                self.log_buffers[name].extend(lines)

        self._handle_flapper()

    def _handle_flapper(self) -> None:
        svc = self._flapper_svc
        if svc is None:
            return
        state = self.states[svc]
        config = self.configs[svc]
        if state.status == ServiceStatus.DEAD:
            return
        if self.tick_count % 600 == 300 and self.rng.random() < 0.7:
            state.memory = 0.96
            state.status = ServiceStatus.DEAD
            state.dead_since_ms = self.time_ms
            wt = self._wall_time(svc)
            lines = self.log_emitter.emit_k8s_oom_log(wt, config)
            self.log_buffers[svc].extend(lines)
            self.k8s_events.append(K8sEvent(
                timestamp_ms=self.time_ms, service=svc,
                pod_name=config.pod_name, event_type="pod_evicted",
                reason="OOMKilled",
                message=f"Pod {config.pod_name} evicted: OOMKilled (flapper)",
            ))

    def _init_streaming(self) -> None:
        scenario_dir = os.path.join(self.output_dir, self.scenario.name)
        for subdir in ["traces", "logs", "metrics", "k8s_events"]:
            os.makedirs(os.path.join(scenario_dir, subdir), exist_ok=True)
        self._stream_files["traces"] = open(
            os.path.join(scenario_dir, "traces", "traces.jsonl"), "w",
        )
        self._stream_files["metrics"] = open(
            os.path.join(scenario_dir, "metrics", "metrics.jsonl"), "w",
        )
        self._stream_files["events"] = open(
            os.path.join(scenario_dir, "k8s_events", "events.jsonl"), "w",
        )
        for svc_name in self.configs:
            for r in range(self.replicas):
                suffix = f"-{r}" if self.replicas > 1 else ""
                key = f"log_{svc_name}{suffix}"
                fname = f"{svc_name}{suffix}.log"
                self._stream_files[key] = open(
                    os.path.join(scenario_dir, "logs", fname), "w",
                )

    def _flush_streaming(self) -> None:
        if not self.streaming:
            return
        for svc_name, lines in self.log_buffers.items():
            if not lines:
                continue
            for r in range(self.replicas):
                suffix = f"-{r}" if self.replicas > 1 else ""
                key = f"log_{svc_name}{suffix}"
                f = self._stream_files.get(key)
                if f:
                    for line in lines:
                        if self.replicas > 1:
                            line = line.replace(
                                self.configs[svc_name].pod_name,
                                f"{self.configs[svc_name].pod_name}-r{r}",
                            )
                        if self.pad_logs:
                            line = self._pad_log_line(line, svc_name)
                        f.write(line + "\n")
        for svc_name in self.log_buffers:
            self.log_buffers[svc_name] = []

        mf = self._stream_files.get("metrics")
        if mf:
            for m in self.metrics_buffer:
                for r in range(self.replicas):
                    suffix = f"-r{r}" if self.replicas > 1 else ""
                    entry = {
                        "timestamp_ms": m.timestamp_ms,
                        "service": m.service,
                        "metric": m.metric,
                        "value": m.value,
                        "labels": {
                            **m.labels,
                            "pod": m.labels.get("pod", "") + suffix,
                        } if suffix else m.labels,
                    }
                    mf.write(json.dumps(entry, separators=(",", ":")) + "\n")
            self.metrics_buffer = []

    def finalize_streaming(self) -> None:
        self._flush_streaming()
        tf = self._stream_files.get("traces")
        if tf:
            write_traces(self.all_spans, self.error_trace_ids, self.rng, tf)
        ef = self._stream_files.get("events")
        if ef:
            for e in self.k8s_events:
                entry = {
                    "timestamp_ms": e.timestamp_ms,
                    "service": e.service,
                    "pod_name": e.pod_name,
                    "event_type": e.event_type,
                    "reason": e.reason,
                    "message": e.message,
                }
                ef.write(json.dumps(entry, separators=(",", ":")) + "\n")
        scenario_dir = os.path.join(self.output_dir, self.scenario.name)
        self._write_ground_truth(scenario_dir)
        self._write_scenario_config(scenario_dir)
        for f in self._stream_files.values():
            f.close()
        self._stream_files = {}

    def _pad_log_line(self, line: str, svc_name: str) -> str:
        config = self.configs[svc_name]
        padding_parts = []

        headers = {
            "X-Request-Id": self._gen_trace_id(),
            "X-Forwarded-For": f"10.{self.rng.randint(0,255)}.{self.rng.randint(0,255)}.{self.rng.randint(1,254)}",
            "X-Forwarded-Proto": "https",
            "X-Real-IP": f"172.16.{self.rng.randint(0,255)}.{self.rng.randint(1,254)}",
            "Host": f"{svc_name}.prod.svc.cluster.local",
            "User-Agent": self.rng.choice([
                "go-http-client/1.1",
                "Java/17.0.2",
                "python-requests/2.28.1",
                "grpc-go/1.53.0",
                "envoy/1.25.0",
            ]),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.{self._gen_trace_id()}.{self._gen_trace_id()}",
            "X-B3-TraceId": self._gen_trace_id(),
            "X-B3-SpanId": self._gen_span_id(),
            "X-B3-Sampled": "1",
            "X-Envoy-Expected-Rq-Timeout-Ms": str(self.rng.choice([1000, 3000, 5000, 10000])),
        }
        padding_parts.append(f" headers={json.dumps(headers)}")

        bodies = [
            {"transaction_id": f"txn_{self._gen_span_id()}", "amount": self.rng.randint(100, 99999) / 100, "currency": "USD", "merchant_id": f"m_{self.rng.randint(1000,9999)}", "card_last4": f"{self.rng.randint(1000,9999)}", "timestamp": self.time_ms},
            {"user_id": f"usr_{self._gen_span_id()}", "session_id": f"sess_{self._gen_trace_id()}", "action": self.rng.choice(["login", "verify", "refresh", "logout"]), "ip": f"10.{self.rng.randint(0,255)}.{self.rng.randint(0,255)}.{self.rng.randint(1,254)}"},
            {"query": f"SELECT * FROM {self.rng.choice(['users','transactions','sessions','audit_log','payments'])} WHERE id = '{self._gen_span_id()}' AND created_at > NOW() - INTERVAL '24 HOURS' ORDER BY created_at DESC LIMIT 100", "params": [self._gen_span_id(), self.time_ms]},
        ]
        padding_parts.append(f" body={json.dumps(self.rng.choice(bodies))}")

        if "ERROR" in line or "error" in line:
            lang = config.language
            if lang == "java":
                frames = [
                    "com.payments.service.TransactionService.processPayment(TransactionService.java:{})".format(self.rng.randint(80, 300)),
                    "com.payments.handler.PaymentHandler.handle(PaymentHandler.java:{})".format(self.rng.randint(40, 150)),
                    "com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:{})".format(self.rng.randint(100, 200)),
                    "com.zaxxer.hikari.pool.PoolBase.newConnection(PoolBase.java:{})".format(self.rng.randint(50, 100)),
                    "java.net.Socket.connect(Socket.java:{})".format(self.rng.randint(500, 700)),
                    "sun.security.ssl.SSLSocketImpl.connect(SSLSocketImpl.java:{})".format(self.rng.randint(200, 400)),
                    "org.apache.http.impl.conn.DefaultHttpClientConnectionOperator.connect(DefaultHttpClientConnectionOperator.java:{})".format(self.rng.randint(100, 200)),
                    "io.netty.channel.AbstractChannelHandlerContext.invokeChannelRead(AbstractChannelHandlerContext.java:{})".format(self.rng.randint(300, 500)),
                    "io.grpc.internal.ServerCallImpl.close(ServerCallImpl.java:{})".format(self.rng.randint(100, 200)),
                    "java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:{})".format(self.rng.randint(1000, 1200)),
                    "java.lang.Thread.run(Thread.java:{})".format(self.rng.randint(700, 900)),
                ]
                n_frames = self.rng.randint(5, len(frames))
                stack = "\n".join(f"\tat {f}" for f in self.rng.sample(frames, n_frames))
                padding_parts.append(f"\n{stack}")
            elif lang == "go":
                frames = [
                    f"goroutine {self.rng.randint(1, 500)} [running]:",
                    f"main.handleRequest(0x{self.rng.randint(0xc000000000, 0xc000ffffff):x}, 0x{self.rng.randint(0xc000000000, 0xc000ffffff):x})",
                    f"\t/app/cmd/server/handler.go:{self.rng.randint(50, 300)}",
                    f"net/http.(*ServeMux).ServeHTTP(0x{self.rng.randint(0xc000000000, 0xc000ffffff):x})",
                    f"\t/usr/local/go/src/net/http/server.go:{self.rng.randint(2000, 3000)}",
                    f"google.golang.org/grpc.(*Server).handleStream(0x{self.rng.randint(0xc000000000, 0xc000ffffff):x})",
                    f"\t/go/pkg/mod/google.golang.org/grpc@v1.53.0/server.go:{self.rng.randint(1500, 2000)}",
                    f"runtime.goexit()",
                    f"\t/usr/local/go/src/runtime/asm_amd64.s:1598",
                ]
                stack = "\n".join(frames)
                padding_parts.append(f"\n{stack}")
            elif lang == "python":
                frames = [
                    'Traceback (most recent call last):',
                    f'  File "/app/{svc_name}/handler.py", line {self.rng.randint(50, 200)}, in handle_request',
                    f'    result = await self.process(request)',
                    f'  File "/app/{svc_name}/service.py", line {self.rng.randint(80, 300)}, in process',
                    f'    response = await self.client.send(payload)',
                    f'  File "/usr/lib/python3.10/asyncio/tasks.py", line {self.rng.randint(200, 400)}, in wait_for',
                    f'    return fut.result()',
                    f'  File "/app/{svc_name}/client.py", line {self.rng.randint(30, 100)}, in send',
                    f'    raise ConnectionError("Connection pool exhausted")',
                    f'ConnectionError: Connection pool exhausted',
                ]
                stack = "\n".join(frames)
                padding_parts.append(f"\n{stack}")

        envoy_meta = {
            "upstream_cluster": f"outbound|8080||{svc_name}.prod.svc.cluster.local",
            "route_name": "default",
            "downstream_local_address": f"10.0.0.{self.rng.randint(1, 20)}:{self.rng.randint(30000, 65000)}",
            "upstream_host": f"10.0.0.{self.rng.randint(1, 20)}:{self.rng.randint(8080, 8090)}",
            "response_flags": self.rng.choice(["", "-", "UO", "URX", "UF", "UT"]),
            "connection_termination_details": "",
            "x_envoy_upstream_service_time": str(self.rng.randint(1, 5000)),
        }
        padding_parts.append(f" envoy={json.dumps(envoy_meta)}")

        return line + "".join(padding_parts)

    def _write_ground_truth(self, scenario_dir: str) -> None:
        gt_path = os.path.join(scenario_dir, "ground_truth.json")
        ground_truth = {
            "scenario": self.scenario.name,
            "faults": [
                {
                    "time_ms": f.time_ms,
                    "target": f.target,
                    "type": f.fault_type,
                    "params": f.params,
                }
                for f in self.scenario.faults
            ],
        }
        with open(gt_path, "w") as f:
            json.dump(ground_truth, f, indent=2)

    def _write_scenario_config(self, scenario_dir: str) -> None:
        sc_path = os.path.join(scenario_dir, "scenario_config.json")
        sc = {
            "name": self.scenario.name,
            "duration_ms": self.scenario.duration_ms,
            "seed": self.scenario.seed,
            "request_rate": self.scenario.request_rate,
            "description": self.scenario.description,
            "services": {
                name: {
                    "language": cfg.language,
                    "log_framework": cfg.log_framework,
                    "zone": cfg.zone,
                    "base_latency_ms": cfg.base_latency_ms,
                    "max_connections": cfg.max_connections,
                    "dependencies": cfg.dependencies,
                    "pod_name": cfg.pod_name,
                }
                for name, cfg in self.configs.items()
            },
            "call_graph": {
                name: cfg.dependencies
                for name, cfg in self.configs.items()
                if cfg.dependencies
            },
        }
        with open(sc_path, "w") as f:
            json.dump(sc, f, indent=2)

    def write_output(self, output_dir: str) -> None:
        scenario_dir = os.path.join(output_dir, self.scenario.name)
        for subdir in ["traces", "logs", "metrics", "k8s_events"]:
            os.makedirs(os.path.join(scenario_dir, subdir), exist_ok=True)

        trace_path = os.path.join(scenario_dir, "traces", "traces.jsonl")
        with open(trace_path, "w") as f:
            write_traces(self.all_spans, self.error_trace_ids, self.rng, f)

        for svc_name, lines in self.log_buffers.items():
            for r in range(self.replicas):
                suffix = f"-r{r}" if self.replicas > 1 else ""
                log_path = os.path.join(
                    scenario_dir, "logs", f"{svc_name}{suffix}.log",
                )
                with open(log_path, "w") as f:
                    for line in lines:
                        if self.replicas > 1:
                            line = line.replace(
                                self.configs[svc_name].pod_name,
                                f"{self.configs[svc_name].pod_name}-r{r}",
                            )
                        if self.pad_logs:
                            line = self._pad_log_line(line, svc_name)
                        f.write(line + "\n")

        metrics_path = os.path.join(scenario_dir, "metrics", "metrics.jsonl")
        with open(metrics_path, "w") as f:
            for m in self.metrics_buffer:
                for r in range(self.replicas):
                    suffix = f"-r{r}" if self.replicas > 1 else ""
                    entry = {
                        "timestamp_ms": m.timestamp_ms,
                        "service": m.service,
                        "metric": m.metric,
                        "value": m.value,
                        "labels": {
                            **m.labels,
                            "pod": m.labels.get("pod", "") + suffix,
                        } if suffix else m.labels,
                    }
                    f.write(json.dumps(entry, separators=(",", ":")) + "\n")

        events_path = os.path.join(scenario_dir, "k8s_events", "events.jsonl")
        with open(events_path, "w") as f:
            for e in self.k8s_events:
                entry = {
                    "timestamp_ms": e.timestamp_ms,
                    "service": e.service,
                    "pod_name": e.pod_name,
                    "event_type": e.event_type,
                    "reason": e.reason,
                    "message": e.message,
                }
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")

        self._write_ground_truth(scenario_dir)
        self._write_scenario_config(scenario_dir)
