from __future__ import annotations

from simulator.models import FaultEvent, ScenarioConfig


def deployment_cascade() -> ScenarioConfig:
    """Bad deployment in payments: pool size set way too high, causing cascade.

    Timeline:
    - 0-10s: steady state, healthy traffic
    - 10s: payments deployment with connection_pool_size=500 (was 50 effectively)
    - 10-70s: cascade develops - payments overwhelms redis/db, gateway times out
    - 70s: rollback triggered
    - 70-120s: system recovers
    """
    return ScenarioConfig(
        name="deployment_cascade",
        duration_ms=120_000,
        seed=42,
        request_rate=5.0,
        description="Bad deployment in payments triggers cascade via connection pool misconfiguration",
        faults=[
            FaultEvent(
                time_ms=10_000, target="payments", fault_type="config_change",
                params={"connection_pool_size": 500, "version": "v2.4.0"},
            ),
            FaultEvent(
                time_ms=70_000, target="payments", fault_type="rollback",
                params={"revert_config": True, "version": "v2.3.1"},
            ),
        ],
    )


def memory_leak() -> ScenarioConfig:
    """Slow memory leak in auth service leading to OOM and cascade."""
    return ScenarioConfig(
        name="memory_leak",
        duration_ms=180_000,
        seed=123,
        request_rate=5.0,
        description="Memory leak in auth service causes OOM, cascading through gateway",
        faults=[
            FaultEvent(
                time_ms=5_000, target="auth", fault_type="memory_leak",
                params={"leak_rate_mb_per_sec": 10},
            ),
        ],
    )


def db_latency_spike() -> ScenarioConfig:
    """External dependency failure: database becomes extremely slow."""
    return ScenarioConfig(
        name="db_latency_spike",
        duration_ms=120_000,
        seed=789,
        request_rate=5.0,
        description="Database latency spike (external dependency) causes payments cascade",
        faults=[
            FaultEvent(
                time_ms=15_000, target="db", fault_type="latency_spike",
                params={"multiplier": 200},
            ),
            FaultEvent(
                time_ms=75_000, target="db", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def redis_failure() -> ScenarioConfig:
    """Redis killed externally, affecting auth and payments."""
    return ScenarioConfig(
        name="redis_failure",
        duration_ms=120_000,
        seed=456,
        request_rate=5.0,
        description="Redis killed externally, cascading to auth and payments",
        faults=[
            FaultEvent(
                time_ms=20_000, target="redis", fault_type="kill",
                params={},
            ),
        ],
    )


SCENARIOS: dict[str, callable] = {
    "deployment_cascade": deployment_cascade,
    "memory_leak": memory_leak,
    "db_latency_spike": db_latency_spike,
    "redis_failure": redis_failure,
}


def get_scenario(name: str) -> ScenarioConfig:
    if name not in SCENARIOS:
        available = ", ".join(sorted(SCENARIOS.keys()))
        raise ValueError(f"Unknown scenario '{name}'. Available: {available}")
    return SCENARIOS[name]()


def list_scenarios() -> list[str]:
    return sorted(SCENARIOS.keys())
