from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from simulator.models import ServiceConfig, ServiceState, ServiceStatus


def _ts_to_datetime(time_ms: int) -> datetime:
    return datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc)


def _fmt_nginx_ts(dt: datetime) -> str:
    return dt.strftime("%d/%b/%Y:%H:%M:%S %z")


def _fmt_python_ts(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f",{ms:03d}"


def _fmt_java_ts(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f".{ms:03d}"


def _fmt_iso_ts(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _fmt_redis_ts(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    return dt.strftime("%-d %b %Y %H:%M:%S") + f".{ms:03d}"


def _fmt_pg_ts(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f".{ms:03d} UTC"


class LogEmitter:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self._thread_counter = 0
        self._pg_pid = 12345
        self._redis_client_id = 48000

    def _next_thread(self) -> int:
        self._thread_counter += 1
        return self._thread_counter

    def _next_pg_pid(self) -> int:
        self._pg_pid += 1
        return self._pg_pid

    def _next_redis_client(self) -> int:
        self._redis_client_id += 1
        return self._redis_client_id

    def emit_request_log(
        self,
        wall_time_ms: int,
        config: ServiceConfig,
        state: ServiceState,
        method: str,
        url: str,
        status_code: int,
        latency_ms: float,
        trace_id: str,
        error_message: str = "",
        downstream_service: str = "",
    ) -> list[str]:
        if state.status == ServiceStatus.DEAD:
            return []
        dt = _ts_to_datetime(wall_time_ms)

        if config.language == "python" and config.name == "gateway":
            return self._gateway_request_log(
                dt, config, state, method, url, status_code, latency_ms,
                trace_id, error_message, downstream_service,
            )
        elif config.language == "java":
            return self._java_request_log(
                dt, config, state, method, url, status_code, latency_ms,
                trace_id, error_message, downstream_service,
            )
        elif config.language == "go":
            return self._go_request_log(
                dt, config, state, method, url, status_code, latency_ms,
                trace_id, error_message, downstream_service,
            )
        elif config.log_framework == "redis-native":
            return self._redis_request_log(
                dt, config, state, status_code, error_message,
            )
        elif config.log_framework == "postgres-native":
            return self._pg_request_log(
                dt, config, state, status_code, error_message,
            )
        elif config.language == "python":
            return self._python_request_log(
                dt, config, state, method, url, status_code, latency_ms,
                trace_id, error_message,
            )
        return []

    def _gateway_request_log(
        self, dt, config, state, method, url, status_code, latency_ms,
        trace_id, error_message, downstream_service,
    ) -> list[str]:
        lines = []
        client_ip = f"10.{self.rng.randint(0,255)}.{self.rng.randint(0,255)}.{self.rng.randint(1,254)}"
        rt = latency_ms / 1000.0
        body_size = 0 if status_code >= 400 else self.rng.randint(200, 5000)
        nginx_line = (
            f'{client_ip} - - [{_fmt_nginx_ts(dt)}] '
            f'"{method} {url} HTTP/1.1" {status_code} {body_size} '
            f'"-" "client/1.0" rt={rt:.3f}'
        )
        lines.append(nginx_line)

        if status_code >= 500:
            target = downstream_service or "upstream"
            pod_hash = f"{self.rng.randint(0x10000,0xfffff):05x}"
            host = f"{target}-{pod_hash}.prod.svc.cluster.local:8080"
            app_line = (
                f"{_fmt_python_ts(dt)} - gateway.proxy - ERROR - "
                f"Upstream timeout: host={host} after {int(latency_ms)}ms, "
                f"retries={self.rng.randint(1,3)}/5"
            )
            lines.append(app_line)
        elif status_code == 504:
            app_line = (
                f"{_fmt_python_ts(dt)} - gateway.proxy - ERROR - "
                f"Gateway timeout: {error_message}"
            )
            lines.append(app_line)
        return lines

    def _java_request_log(
        self, dt, config, state, method, url, status_code, latency_ms,
        trace_id, error_message, downstream_service,
    ) -> list[str]:
        lines = []
        thread = f"pool-3-thread-{self.rng.randint(1, 50)}"

        if state.connections >= config.max_connections * 0.9:
            active = min(state.connections, config.max_connections)
            pending = max(0, state.connections - config.max_connections)
            wait = int(latency_ms)
            line = (
                f"{_fmt_java_ts(dt)} ERROR [{config.name}] [{thread}] "
                f"c.h.p.ConnectionManager - Pool exhausted: "
                f"active={active} max={config.max_connections} "
                f"pending={pending} wait_ms={wait}"
            )
            lines.append(line)

        if error_message and downstream_service:
            line = (
                f"{_fmt_java_ts(dt)} ERROR [{config.name}] [{thread}] "
                f"c.p.s.TransactionService - {downstream_service} call failed: "
                f"{error_message}"
            )
            lines.append(line)

        if state.cpu > 0.85:
            delta_s = self.rng.randint(10, 60)
            delta_ms = self.rng.randint(0, 999)
            line = (
                f"{_fmt_java_ts(dt)} WARN [{config.name}] [{thread}] "
                f"com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - "
                f"Thread starvation or clock leap detected "
                f"(housekeeper delta={delta_s}s{delta_ms}ms)."
            )
            lines.append(line)
        return lines

    def _go_request_log(
        self, dt, config, state, method, url, status_code, latency_ms,
        trace_id, error_message, downstream_service,
    ) -> list[str]:
        lines = []
        ts = _fmt_iso_ts(dt)

        if status_code >= 500:
            caller_file = "handler.go"
            caller_line = self.rng.randint(80, 300)
            if downstream_service == "redis":
                caller_file = "redis.go"
                caller_line = self.rng.randint(140, 200)
                ip = "10.0.0.5"
                entry = {
                    "level": "error",
                    "time": ts,
                    "caller": f"{caller_file}:{caller_line}",
                    "msg": "redis connection failed",
                    "error": f"dial tcp {ip}:6379: connect: connection refused",
                    "retry": self.rng.randint(1, 5),
                    "trace_id": trace_id,
                    "service": config.name,
                }
            elif downstream_service == "user-store":
                caller_file = "userstore.go"
                caller_line = self.rng.randint(50, 120)
                entry = {
                    "level": "error",
                    "time": ts,
                    "caller": f"{caller_file}:{caller_line}",
                    "msg": "user-store lookup failed",
                    "error": f"rpc error: code = Unavailable desc = connection refused",
                    "retry": self.rng.randint(1, 3),
                    "trace_id": trace_id,
                    "service": config.name,
                }
            else:
                entry = {
                    "level": "error",
                    "time": ts,
                    "caller": f"{caller_file}:{caller_line}",
                    "msg": error_message or "request failed",
                    "status_code": status_code,
                    "trace_id": trace_id,
                    "service": config.name,
                }
            lines.append(json.dumps(entry, separators=(",", ":")))
        return lines

    def _redis_request_log(
        self, dt, config, state, status_code, error_message,
    ) -> list[str]:
        lines = []
        ts = _fmt_redis_ts(dt)

        if state.connections >= config.max_connections:
            lines.append(
                f"1:M {ts} # max number of clients reached ({config.max_connections})"
            )
        elif status_code >= 500:
            cid = self._next_redis_client()
            port = self.rng.randint(30000, 65000)
            caller_ip = f"10.0.0.{self.rng.randint(1, 20)}"
            lines.append(
                f"1:M {ts} # Client id={cid} addr={caller_ip}:{port} "
                f"scheduled to be closed ASAP for overcoming of output buffer limits."
            )
        return lines

    def _pg_request_log(
        self, dt, config, state, status_code, error_message,
    ) -> list[str]:
        lines = []
        ts = _fmt_pg_ts(dt)

        if state.connections >= config.max_connections:
            pid = self._next_pg_pid()
            lines.append(
                f"{ts} [{pid}] ERROR:  remaining connection slots are reserved "
                f"for non-replication superuser connections"
            )
        elif status_code >= 500:
            pid = self._next_pg_pid()
            lines.append(
                f"{ts} [{pid}] LOG:  could not accept SSL connection: EOF detected"
            )
        return lines

    def _python_request_log(
        self, dt, config, state, method, url, status_code, latency_ms,
        trace_id, error_message,
    ) -> list[str]:
        lines = []
        if status_code >= 500:
            lines.append(
                f"{_fmt_python_ts(dt)} - {config.name}.handler - ERROR - "
                f"Request failed: {method} {url} -> {status_code}: {error_message}"
            )
        return lines

    def emit_deployment_log(
        self, wall_time_ms: int, config: ServiceConfig,
        version: str, action: str,
    ) -> list[str]:
        dt = _ts_to_datetime(wall_time_ms)
        lines = []
        if config.language == "java":
            if action == "start":
                lines.append(
                    f"{_fmt_java_ts(dt)} INFO [{config.name}] [main] "
                    f"c.h.p.DeploymentManager - Starting deployment {version}"
                )
            elif action == "complete":
                lines.append(
                    f"{_fmt_java_ts(dt)} INFO [{config.name}] [main] "
                    f"c.h.p.DeploymentManager - Deployment {version} complete"
                )
            elif action == "rollback":
                lines.append(
                    f"{_fmt_java_ts(dt)} WARN [{config.name}] [main] "
                    f"c.h.p.DeploymentManager - Rolling back to {version}"
                )
        elif config.language == "go":
            ts = _fmt_iso_ts(dt)
            entry = {
                "level": "info",
                "time": ts,
                "caller": "deploy.go:42",
                "msg": f"deployment {action}",
                "version": version,
                "service": config.name,
            }
            lines.append(json.dumps(entry, separators=(",", ":")))
        elif config.language == "python":
            if action == "start":
                lines.append(
                    f"{_fmt_python_ts(dt)} - {config.name}.deploy - INFO - "
                    f"Starting deployment {version}"
                )
            elif action == "rollback":
                lines.append(
                    f"{_fmt_python_ts(dt)} - {config.name}.deploy - WARN - "
                    f"Rolling back to {version}"
                )
        return lines

    def emit_gc_log(
        self, wall_time_ms: int, config: ServiceConfig,
        state: ServiceState,
    ) -> list[str]:
        if config.language != "java":
            return []
        dt = _ts_to_datetime(wall_time_ms)
        pause_ms = self.rng.randint(50, 500)
        heap_before = int(state.memory * 2048)
        heap_after = int(state.memory * 2048 * 0.7)
        thread = f"pool-3-thread-{self.rng.randint(1, 50)}"
        return [
            f"{_fmt_java_ts(dt)} INFO [{config.name}] [{thread}] "
            f"c.p.s.GCMonitor - GC pause: {pause_ms}ms, "
            f"heap {heap_before}M->{heap_after}M"
        ]

    def emit_health_check_log(
        self, wall_time_ms: int, config: ServiceConfig,
        state: ServiceState,
    ) -> list[str]:
        dt = _ts_to_datetime(wall_time_ms)
        if config.language == "go":
            ts = _fmt_iso_ts(dt)
            entry = {
                "level": "debug",
                "time": ts,
                "caller": "health.go:23",
                "msg": "health check",
                "status": state.status.value.lower(),
                "service": config.name,
            }
            return [json.dumps(entry, separators=(",", ":"))]
        elif config.language == "python":
            return [
                f"{_fmt_python_ts(dt)} - {config.name}.health - DEBUG - "
                f"Health check: status={state.status.value}"
            ]
        elif config.language == "java":
            return [
                f"{_fmt_java_ts(dt)} DEBUG [{config.name}] [health-check-1] "
                f"c.p.s.HealthCheck - status={state.status.value}"
            ]
        return []

    def emit_pool_stats_log(
        self, wall_time_ms: int, config: ServiceConfig,
        state: ServiceState,
    ) -> list[str]:
        dt = _ts_to_datetime(wall_time_ms)
        active = state.connections
        idle = max(0, config.max_connections - state.connections)
        if config.language == "go":
            ts = _fmt_iso_ts(dt)
            entry = {
                "level": "debug",
                "time": ts,
                "caller": "pool.go:89",
                "msg": "connection pool stats",
                "active": active,
                "idle": idle,
                "max": config.max_connections,
            }
            return [json.dumps(entry, separators=(",", ":"))]
        elif config.language == "java":
            thread = f"pool-3-thread-{self.rng.randint(1, 50)}"
            return [
                f"{_fmt_java_ts(dt)} DEBUG [{config.name}] [{thread}] "
                f"com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - "
                f"Pool stats (total={config.max_connections}, active={active}, "
                f"idle={idle}, waiting=0)"
            ]
        return []

    def emit_slow_query_log(
        self, wall_time_ms: int, config: ServiceConfig,
        duration_ms: int,
    ) -> list[str]:
        if config.log_framework != "postgres-native":
            return []
        dt = _ts_to_datetime(wall_time_ms)
        ts = _fmt_pg_ts(dt)
        pid = self._next_pg_pid()
        tables = ["users", "transactions", "sessions", "audit_log"]
        table = self.rng.choice(tables)
        return [
            f"{ts} [{pid}] LOG:  duration: {duration_ms}.{self.rng.randint(0,999):03d} ms  "
            f"statement: SELECT * FROM {table} WHERE updated_at > now() - interval '5 minutes'"
        ]

    def emit_k8s_oom_log(
        self, wall_time_ms: int, config: ServiceConfig,
    ) -> list[str]:
        dt = _ts_to_datetime(wall_time_ms)
        if config.language == "go":
            ts = _fmt_iso_ts(dt)
            entry = {
                "level": "fatal",
                "time": ts,
                "caller": "main.go:1",
                "msg": "out of memory",
                "service": config.name,
            }
            return [json.dumps(entry, separators=(",", ":"))]
        elif config.language == "python":
            return [
                f"{_fmt_python_ts(dt)} - {config.name} - CRITICAL - "
                f"MemoryError: out of memory"
            ]
        elif config.language == "java":
            return [
                f"{_fmt_java_ts(dt)} ERROR [{config.name}] [main] "
                f"c.p.s.Application - java.lang.OutOfMemoryError: Java heap space"
            ]
        return []

    def emit_tls_warning(
        self, wall_time_ms: int, config: ServiceConfig,
        days_until_expiry: int,
    ) -> list[str]:
        dt = _ts_to_datetime(wall_time_ms)
        if config.language == "go":
            ts = _fmt_iso_ts(dt)
            entry = {
                "level": "warn",
                "time": ts,
                "caller": "tls.go:67",
                "msg": "TLS certificate expiring soon",
                "days_remaining": days_until_expiry,
                "service": config.name,
            }
            return [json.dumps(entry, separators=(",", ":"))]
        elif config.language == "python":
            return [
                f"{_fmt_python_ts(dt)} - {config.name}.tls - WARNING - "
                f"TLS certificate expires in {days_until_expiry} days"
            ]
        return []

    def emit_redis_background_log(
        self, wall_time_ms: int, config: ServiceConfig,
        state: ServiceState,
    ) -> list[str]:
        dt = _ts_to_datetime(wall_time_ms)
        ts = _fmt_redis_ts(dt)
        mem_mb = int(state.memory * 8192)
        return [
            f"1:M {ts} * DB 0: {self.rng.randint(1000,50000)} keys "
            f"({self.rng.randint(1,100)} volatile) in {self.rng.randint(1,16)} slots "
            f"HT. {mem_mb}M used memory"
        ]


def state_max_conn(config: ServiceConfig, state: ServiceState) -> int:
    return config.max_connections
