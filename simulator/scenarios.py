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


def traffic_surge() -> ScenarioConfig:
    """Sudden traffic spike overwhelms gateway, saturating both payments and auth.

    Timeline:
    - 0-10s: normal 5 RPS
    - 10s: traffic jumps to 50 RPS
    - 10-60s: gateway, payments, auth all hit connection limits
    - 60s: traffic drops back to normal
    - 60-120s: system drains and recovers
    """
    return ScenarioConfig(
        name="traffic_surge",
        duration_ms=120_000,
        seed=314,
        request_rate=50.0,
        description="10x traffic spike overwhelms all frontend services simultaneously",
        faults=[
            FaultEvent(
                time_ms=60_000, target="gateway", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def multi_fault() -> ScenarioConfig:
    """Two independent faults at the same time — hard RCA problem.

    Timeline:
    - 0-10s: steady state
    - 10s: redis latency spike begins (affects auth + payments)
    - 15s: auth starts leaking memory (independent root cause)
    - 10-90s: both failures compound — auth path has two root causes
    - 90s: redis recovers, but auth still leaking
    - 90-180s: auth eventually OOMs, restarts, partial recovery
    """
    return ScenarioConfig(
        name="multi_fault",
        duration_ms=180_000,
        seed=271,
        request_rate=5.0,
        description="Simultaneous redis latency spike and auth memory leak — two independent root causes",
        faults=[
            FaultEvent(
                time_ms=10_000, target="redis", fault_type="latency_spike",
                params={"multiplier": 150},
            ),
            FaultEvent(
                time_ms=15_000, target="auth", fault_type="memory_leak",
                params={"leak_rate_mb_per_sec": 8},
            ),
            FaultEvent(
                time_ms=90_000, target="redis", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def rolling_restart_failure() -> ScenarioConfig:
    """Rolling restart gone wrong: auth killed, then payments killed before auth recovers.

    Timeline:
    - 0-10s: steady state
    - 10s: auth killed for maintenance restart
    - 13s: before auth is back (takes 3s), payments also killed
    - 10-16s: both critical services down, gateway gets 100% errors
    - 13-16s: auth comes back but payments still dead
    - 16s: payments comes back
    - 16-60s: thundering herd as both recover under load
    """
    return ScenarioConfig(
        name="rolling_restart_failure",
        duration_ms=60_000,
        seed=161,
        request_rate=5.0,
        description="Botched rolling restart: auth and payments killed in overlapping windows",
        faults=[
            FaultEvent(
                time_ms=10_000, target="auth", fault_type="kill",
                params={},
            ),
            FaultEvent(
                time_ms=13_000, target="payments", fault_type="kill",
                params={},
            ),
        ],
    )


def user_store_latency() -> ScenarioConfig:
    """Slow user-store only affects auth path, payments path stays healthy.

    Timeline:
    - 0-15s: steady state
    - 15s: user-store latency spikes 100x (simulating slow backend query)
    - 15-80s: auth path degrades, payments path unaffected
    - 80s: user-store recovers
    - 80-120s: auth path recovers
    """
    return ScenarioConfig(
        name="user_store_latency",
        duration_ms=120_000,
        seed=577,
        request_rate=5.0,
        description="User-store latency spike degrades only auth path while payments stays healthy",
        faults=[
            FaultEvent(
                time_ms=15_000, target="user-store", fault_type="latency_spike",
                params={"multiplier": 100},
            ),
            FaultEvent(
                time_ms=80_000, target="user-store", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def cascading_kill() -> ScenarioConfig:
    """Redis dies, taking payments with it via connection exhaustion, then gateway.

    Timeline:
    - 0-10s: steady state
    - 10s: redis killed
    - 10-30s: payments errors spike from dead redis dependency
    - 30s: payments killed (simulating OOM from retry storm)
    - 30-40s: gateway has both downstream services dead
    - 40s: redis restarts (after 3s dead)
    - 43s: payments restarts
    - 43-90s: recovery with thundering herd
    """
    return ScenarioConfig(
        name="cascading_kill",
        duration_ms=90_000,
        seed=999,
        request_rate=5.0,
        description="Redis death cascades to payments kill, then gateway — domino failure",
        faults=[
            FaultEvent(
                time_ms=10_000, target="redis", fault_type="kill",
                params={},
            ),
            FaultEvent(
                time_ms=30_000, target="payments", fault_type="kill",
                params={},
            ),
        ],
    )


def intermittent_errors() -> ScenarioConfig:
    """Flapping error injection on payments — comes and goes, hard to diagnose.

    Timeline:
    - 0-15s: steady state
    - 15s: payments starts throwing 50% errors
    - 35s: errors stop
    - 55s: errors come back at 30%
    - 75s: errors stop again
    - 95s: one more burst at 70%
    - 105s: finally stops
    - 105-120s: stable recovery
    """
    return ScenarioConfig(
        name="intermittent_errors",
        duration_ms=120_000,
        seed=404,
        request_rate=5.0,
        description="Intermittent error injection on payments — flapping fault, hard to root-cause",
        faults=[
            FaultEvent(
                time_ms=15_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.5},
            ),
            FaultEvent(
                time_ms=35_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
            FaultEvent(
                time_ms=55_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.3},
            ),
            FaultEvent(
                time_ms=75_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
            FaultEvent(
                time_ms=95_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.7},
            ),
            FaultEvent(
                time_ms=105_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def payments_memory_leak() -> ScenarioConfig:
    """Memory leak in payments (Java service) — GC storms + connection flood compound into errors.

    Timeline:
    - 0-10s: steady state
    - 10s: payments starts leaking memory + connection pool grows
    - 10-50s: Java GC fires repeatedly (CPU spikes, latency degrades),
              connection pool floods downstream, db/redis get overwhelmed
    - 50s: rollback stops the leak and pool
    - 50-90s: recovery
    """
    return ScenarioConfig(
        name="payments_memory_leak",
        duration_ms=90_000,
        seed=667,
        request_rate=5.0,
        description="Payments memory leak + connection pool growth — GC storms compound with connection pressure",
        faults=[
            FaultEvent(
                time_ms=10_000, target="payments", fault_type="memory_leak",
                params={"leak_rate_mb_per_sec": 50},
            ),
            FaultEvent(
                time_ms=10_000, target="payments", fault_type="config_change",
                params={"connection_pool_size": 250, "version": "v2.7.0-leak"},
            ),
            FaultEvent(
                time_ms=50_000, target="payments", fault_type="rollback",
                params={"revert_config": True, "version": "v2.3.1"},
            ),
        ],
    )


def db_connection_flood() -> ScenarioConfig:
    """Payments misconfigured to open too many DB connections, starving the database.

    Timeline:
    - 0-10s: steady state
    - 10s: payments pool size cranked to 300 (db max is 200)
    - 10-50s: db connections exhausted, payments errors cascade to gateway
    - 50s: config corrected back
    - 50-90s: recovery
    """
    return ScenarioConfig(
        name="db_connection_flood",
        duration_ms=90_000,
        seed=808,
        request_rate=5.0,
        description="Payments floods database connection pool beyond db max_connections limit",
        faults=[
            FaultEvent(
                time_ms=10_000, target="payments", fault_type="config_change",
                params={"connection_pool_size": 300, "version": "v2.5.0-beta"},
            ),
            FaultEvent(
                time_ms=50_000, target="payments", fault_type="rollback",
                params={"revert_config": True, "version": "v2.3.1"},
            ),
        ],
    )


def auth_error_spike() -> ScenarioConfig:
    """Auth service starts returning errors — only auth-dependent paths fail.

    Timeline:
    - 0-10s: steady state
    - 10s: auth starts throwing 60% errors (bad deploy, logic bug)
    - 10-60s: gateway auth paths fail, payment paths healthy
    - 60s: auth fix deployed
    - 60-90s: recovery
    """
    return ScenarioConfig(
        name="auth_error_spike",
        duration_ms=90_000,
        seed=503,
        request_rate=5.0,
        description="Auth service error spike — only auth-dependent request paths degrade",
        faults=[
            FaultEvent(
                time_ms=10_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.6},
            ),
            FaultEvent(
                time_ms=60_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def slow_burn_cascade() -> ScenarioConfig:
    """Gradual degradation: small latency increase in db compounds over time.

    Timeline:
    - 0-20s: steady state
    - 20s: db latency 5x (subtle — 3ms * 5 = 15ms, not immediately alarming)
    - 40s: db latency 20x (60ms, payments starts feeling it)
    - 60s: db latency 100x (300ms, cascade begins)
    - 80s: db latency 300x (900ms, full cascade)
    - 100s: db recovers
    - 100-150s: system drains
    """
    return ScenarioConfig(
        name="slow_burn_cascade",
        duration_ms=150_000,
        seed=142,
        request_rate=5.0,
        description="Gradually worsening db latency — slow burn that escalates into full cascade",
        faults=[
            FaultEvent(
                time_ms=20_000, target="db", fault_type="latency_spike",
                params={"multiplier": 5},
            ),
            FaultEvent(
                time_ms=40_000, target="db", fault_type="latency_spike",
                params={"multiplier": 20},
            ),
            FaultEvent(
                time_ms=60_000, target="db", fault_type="latency_spike",
                params={"multiplier": 100},
            ),
            FaultEvent(
                time_ms=80_000, target="db", fault_type="latency_spike",
                params={"multiplier": 300},
            ),
            FaultEvent(
                time_ms=100_000, target="db", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def total_meltdown() -> ScenarioConfig:
    """Everything goes wrong — three faults in rapid succession.

    Timeline:
    - 0-5s: steady state
    - 5s: db latency 200x
    - 8s: redis killed
    - 12s: payments gets error injection 80%
    - 5-60s: complete system failure across all paths
    - 60s: db recovers
    - 63s: redis restarts
    - 65s: payments errors stop
    - 65-120s: slow recovery from total meltdown
    """
    return ScenarioConfig(
        name="total_meltdown",
        duration_ms=120_000,
        seed=666,
        request_rate=5.0,
        description="Three simultaneous faults across db, redis, and payments — total system failure",
        faults=[
            FaultEvent(
                time_ms=5_000, target="db", fault_type="latency_spike",
                params={"multiplier": 200},
            ),
            FaultEvent(
                time_ms=8_000, target="redis", fault_type="kill",
                params={},
            ),
            FaultEvent(
                time_ms=12_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.8},
            ),
            FaultEvent(
                time_ms=60_000, target="db", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
            FaultEvent(
                time_ms=65_000, target="payments", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def gateway_overload() -> ScenarioConfig:
    """Gateway bad deploy: latency spike + error injection simulating middleware bug.

    Timeline:
    - 0-10s: steady state
    - 10s: gateway deploy — latency 30x + 40% errors (bad request validation middleware)
    - 10-50s: all paths fail at the gateway level, downstream services look healthy
    - 50s: gateway rollback
    - 50-90s: recovery
    """
    return ScenarioConfig(
        name="gateway_overload",
        duration_ms=90_000,
        seed=720,
        request_rate=5.0,
        description="Gateway bad deploy causes errors at entry point — downstream services healthy",
        faults=[
            FaultEvent(
                time_ms=10_000, target="gateway", fault_type="latency_spike",
                params={"multiplier": 30},
            ),
            FaultEvent(
                time_ms=10_000, target="gateway", fault_type="error_injection",
                params={"error_rate": 0.4},
            ),
            FaultEvent(
                time_ms=50_000, target="gateway", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
            FaultEvent(
                time_ms=50_000, target="gateway", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def redis_latency_auth_kill() -> ScenarioConfig:
    """Redis goes slow, auth can't handle it and dies — misleading root cause.

    The RCA challenge: redis is slow, but auth OOMs from retry-induced memory pressure.
    Root cause is redis, but the visible symptom is auth dying.

    Timeline:
    - 0-10s: steady state
    - 10s: redis latency 80x (slow but not dead)
    - 10-40s: auth retries pile up, memory climbs
    - 25s: auth memory leak starts (simulating retry buffer growth)
    - ~50s: auth OOMs — the visible symptom
    - redis stays slow throughout
    - 70s: redis recovers
    - 70-120s: auth restarts, system recovers
    """
    return ScenarioConfig(
        name="redis_latency_auth_kill",
        duration_ms=120_000,
        seed=333,
        request_rate=5.0,
        description="Redis latency causes auth to OOM — misleading symptom, root cause is redis",
        faults=[
            FaultEvent(
                time_ms=10_000, target="redis", fault_type="latency_spike",
                params={"multiplier": 80},
            ),
            FaultEvent(
                time_ms=25_000, target="auth", fault_type="memory_leak",
                params={"leak_rate_mb_per_sec": 15},
            ),
            FaultEvent(
                time_ms=70_000, target="redis", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def double_deploy_conflict() -> ScenarioConfig:
    """Two deployments at the same time — payments and auth both get bad configs.

    Timeline:
    - 0-10s: steady state
    - 10s: payments deployed with huge pool (connection flood)
    - 12s: auth deployed with error-prone code
    - 10-50s: both paths failing for different reasons
    - 50s: payments rolled back
    - 55s: auth errors fixed
    - 55-90s: recovery
    """
    return ScenarioConfig(
        name="double_deploy_conflict",
        duration_ms=90_000,
        seed=202,
        request_rate=5.0,
        description="Simultaneous bad deployments to payments and auth — two distinct deploy issues",
        faults=[
            FaultEvent(
                time_ms=10_000, target="payments", fault_type="config_change",
                params={"connection_pool_size": 400, "version": "v2.6.0"},
            ),
            FaultEvent(
                time_ms=12_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.5},
            ),
            FaultEvent(
                time_ms=50_000, target="payments", fault_type="rollback",
                params={"revert_config": True, "version": "v2.3.1"},
            ),
            FaultEvent(
                time_ms=55_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def needle_in_haystack() -> ScenarioConfig:
    """Real root cause: db latency spike. Red herrings: auth memory leak (unrelated),
    user-store errors (unrelated), redis brief kill (unrelated, recovers fast).

    The RCA agent must identify the db latency as the actual root cause of payments
    failures while ignoring three concurrent but unrelated faults.

    Timeline:
    - 0-10s: steady state
    - 8s: [RED HERRING] auth starts leaking memory (slow, won't OOM for a while)
    - 12s: [RED HERRING] user-store gets 20% error injection (affects some auth paths)
    - 15s: [ROOT CAUSE] db latency 200x — payments cascade begins
    - 20s: [RED HERRING] redis killed briefly (auto-restarts in 3s)
    - 30s: [RED HERRING] user-store errors stop
    - 60s: db latency recovers
    - 60-120s: system recovers, auth still leaking but hasn't OOM'd
    """
    return ScenarioConfig(
        name="needle_in_haystack",
        duration_ms=120_000,
        seed=1337,
        request_rate=5.0,
        description="DB latency is the real root cause, buried under 3 concurrent red-herring faults",
        faults=[
            FaultEvent(
                time_ms=8_000, target="auth", fault_type="memory_leak",
                params={"leak_rate_mb_per_sec": 3},
            ),
            FaultEvent(
                time_ms=12_000, target="user-store", fault_type="error_injection",
                params={"error_rate": 0.2},
            ),
            FaultEvent(
                time_ms=15_000, target="db", fault_type="latency_spike",
                params={"multiplier": 200},
            ),
            FaultEvent(
                time_ms=20_000, target="redis", fault_type="kill",
                params={},
            ),
            FaultEvent(
                time_ms=30_000, target="user-store", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
            FaultEvent(
                time_ms=60_000, target="db", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def ghost_in_the_machine() -> ScenarioConfig:
    """Five faults, only two are the real cause. The rest are noise.

    Real causes: payments config_change (pool flood) + redis latency spike (compounds it).
    Red herrings: auth brief error burst (unrelated, stops on its own),
    user-store latency blip (unrelated, too mild to cascade),
    db brief kill (unrelated, auto-recovers).

    Timeline:
    - 0-5s: steady state
    - 5s: [RED HERRING] auth 30% errors for 10 seconds
    - 8s: [ROOT CAUSE 1] payments pool flooded to 400
    - 10s: [RED HERRING] user-store latency 3x (barely noticeable)
    - 12s: [ROOT CAUSE 2] redis latency 100x (compounds payments cascade)
    - 15s: auth errors stop (red herring clears)
    - 20s: [RED HERRING] db killed briefly
    - 25s: user-store latency recovers
    - 60s: payments rolled back
    - 65s: redis recovers
    - 65-120s: system recovers
    """
    return ScenarioConfig(
        name="ghost_in_the_machine",
        duration_ms=120_000,
        seed=1984,
        request_rate=5.0,
        description="Two real root causes buried among three red-herring faults — the 'ghost' scenario",
        faults=[
            FaultEvent(
                time_ms=5_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.3},
            ),
            FaultEvent(
                time_ms=8_000, target="payments", fault_type="config_change",
                params={"connection_pool_size": 400, "version": "v2.8.0-canary"},
            ),
            FaultEvent(
                time_ms=10_000, target="user-store", fault_type="latency_spike",
                params={"multiplier": 3},
            ),
            FaultEvent(
                time_ms=12_000, target="redis", fault_type="latency_spike",
                params={"multiplier": 100},
            ),
            FaultEvent(
                time_ms=15_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
            FaultEvent(
                time_ms=20_000, target="db", fault_type="kill",
                params={},
            ),
            FaultEvent(
                time_ms=25_000, target="user-store", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
            FaultEvent(
                time_ms=60_000, target="payments", fault_type="rollback",
                params={"revert_config": True, "version": "v2.3.1"},
            ),
            FaultEvent(
                time_ms=65_000, target="redis", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def blame_the_wrong_service() -> ScenarioConfig:
    """Redis is killed, but the real damage is from payments' retry storm that kills db.

    The obvious suspect is redis (it died first), but the actual cascade path is:
    redis dies → payments retries overwhelm db → db exhausts connections → payments errors.
    Redis itself recovers in 3s, but the db damage persists much longer.

    Timeline:
    - 0-10s: steady state
    - 10s: redis killed
    - 10-13s: redis dead, payments retries start
    - 13s: redis back, but payments retry storm already overloaded db
    - 13-25s: [RED HERRING] auth gets unrelated 15% error injection (bad config)
    - 15s: [RED HERRING] user-store latency 5x (maintenance)
    - 20-60s: db is the real bottleneck (connections exhausted from retry storm)
    - 30s: user-store latency recovers
    - 40s: auth errors stop
    - 60-90s: db slowly recovers
    """
    return ScenarioConfig(
        name="blame_the_wrong_service",
        duration_ms=90_000,
        seed=2024,
        request_rate=5.0,
        description="Redis dies briefly but db is the real victim — retry storm misdirection",
        faults=[
            FaultEvent(
                time_ms=10_000, target="redis", fault_type="kill",
                params={},
            ),
            FaultEvent(
                time_ms=13_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.15},
            ),
            FaultEvent(
                time_ms=15_000, target="user-store", fault_type="latency_spike",
                params={"multiplier": 5},
            ),
            FaultEvent(
                time_ms=30_000, target="user-store", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
            FaultEvent(
                time_ms=40_000, target="auth", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def chaos_monkey() -> ScenarioConfig:
    """Simulated chaos engineering run: random kills across services with noise.

    Multiple services get killed at staggered intervals (like a chaos monkey run),
    plus unrelated latency and error faults to confuse the RCA.

    Timeline:
    - 5s: auth killed
    - 10s: [NOISE] db latency 3x
    - 15s: payments killed (while auth is restarting)
    - 20s: db latency recovers
    - 25s: [NOISE] user-store 10% errors
    - 30s: redis killed (payments recovering, auth back)
    - 40s: user-store errors stop
    - 45s: [NOISE] gateway latency 5x
    - 55s: gateway latency recovers
    - 60s: db killed
    - 80-120s: everything recovering
    """
    return ScenarioConfig(
        name="chaos_monkey",
        duration_ms=120_000,
        seed=9999,
        request_rate=5.0,
        description="Chaos monkey: staggered kills across 4 services with noise faults between",
        faults=[
            FaultEvent(time_ms=5_000, target="auth", fault_type="kill", params={}),
            FaultEvent(time_ms=10_000, target="db", fault_type="latency_spike", params={"multiplier": 3}),
            FaultEvent(time_ms=15_000, target="payments", fault_type="kill", params={}),
            FaultEvent(time_ms=20_000, target="db", fault_type="latency_spike", params={"multiplier": 1}),
            FaultEvent(time_ms=25_000, target="user-store", fault_type="error_injection", params={"error_rate": 0.1}),
            FaultEvent(time_ms=30_000, target="redis", fault_type="kill", params={}),
            FaultEvent(time_ms=40_000, target="user-store", fault_type="error_injection", params={"error_rate": 0.0}),
            FaultEvent(time_ms=45_000, target="gateway", fault_type="latency_spike", params={"multiplier": 5}),
            FaultEvent(time_ms=55_000, target="gateway", fault_type="latency_spike", params={"multiplier": 1}),
            FaultEvent(time_ms=60_000, target="db", fault_type="kill", params={}),
        ],
    )


def the_perfect_storm() -> ScenarioConfig:
    """Six faults across all service tiers, overlapping in complex ways.

    Root causes: payments deploy (pool flood) and db latency spike.
    Red herrings: auth memory leak (slow, won't OOM during scenario),
    redis brief errors, user-store kill, gateway latency blip.

    The challenge: identify that payments pool + db latency are the interacting
    root causes while 4 other faults create noise across every service.

    Timeline:
    - 3s: [RED HERRING] auth memory leak (very slow)
    - 5s: [RED HERRING] redis 10% errors
    - 8s: [ROOT CAUSE] payments pool flood 350
    - 10s: [ROOT CAUSE] db latency 150x
    - 15s: [RED HERRING] user-store killed
    - 18s: user-store back
    - 20s: [RED HERRING] gateway latency 10x for 10s
    - 25s: redis errors stop
    - 30s: gateway latency recovers
    - 60s: payments rolled back
    - 70s: db latency recovers
    - 70-120s: system recovery
    """
    return ScenarioConfig(
        name="the_perfect_storm",
        duration_ms=120_000,
        seed=7777,
        request_rate=5.0,
        description="6 overlapping faults across all tiers — 2 real root causes, 4 red herrings",
        faults=[
            FaultEvent(time_ms=3_000, target="auth", fault_type="memory_leak", params={"leak_rate_mb_per_sec": 2}),
            FaultEvent(time_ms=5_000, target="redis", fault_type="error_injection", params={"error_rate": 0.1}),
            FaultEvent(time_ms=8_000, target="payments", fault_type="config_change", params={"connection_pool_size": 350, "version": "v3.0.0-rc1"}),
            FaultEvent(time_ms=10_000, target="db", fault_type="latency_spike", params={"multiplier": 150}),
            FaultEvent(time_ms=15_000, target="user-store", fault_type="kill", params={}),
            FaultEvent(time_ms=20_000, target="gateway", fault_type="latency_spike", params={"multiplier": 10}),
            FaultEvent(time_ms=25_000, target="redis", fault_type="error_injection", params={"error_rate": 0.0}),
            FaultEvent(time_ms=30_000, target="gateway", fault_type="latency_spike", params={"multiplier": 1}),
            FaultEvent(time_ms=60_000, target="payments", fault_type="rollback", params={"revert_config": True, "version": "v2.3.1"}),
            FaultEvent(time_ms=70_000, target="db", fault_type="latency_spike", params={"multiplier": 1}),
        ],
    )


def false_recovery() -> ScenarioConfig:
    """System appears to recover, then gets hit again — tests temporal reasoning.

    First wave: payments errors (real fault).
    Brief recovery window where everything looks healthy.
    Second wave: db latency spike (different root cause).
    Plus noise faults scattered throughout.

    Timeline:
    - 5s: [WAVE 1] payments 50% errors
    - 8s: [RED HERRING] auth latency 3x
    - 15s: auth latency recovers
    - 20s: payments errors stop — system "recovers"
    - 20-35s: quiet recovery period (everything looks healthy)
    - 35s: [WAVE 2] db latency 200x (new, unrelated root cause)
    - 38s: [RED HERRING] redis 15% errors
    - 45s: redis errors stop
    - 50s: [RED HERRING] user-store killed briefly
    - 70s: db recovers
    - 70-120s: final recovery
    """
    return ScenarioConfig(
        name="false_recovery",
        duration_ms=120_000,
        seed=5050,
        request_rate=5.0,
        description="System recovers then fails again from different root cause — two-wave incident with noise",
        faults=[
            FaultEvent(time_ms=5_000, target="payments", fault_type="error_injection", params={"error_rate": 0.5}),
            FaultEvent(time_ms=8_000, target="auth", fault_type="latency_spike", params={"multiplier": 3}),
            FaultEvent(time_ms=15_000, target="auth", fault_type="latency_spike", params={"multiplier": 1}),
            FaultEvent(time_ms=20_000, target="payments", fault_type="error_injection", params={"error_rate": 0.0}),
            FaultEvent(time_ms=35_000, target="db", fault_type="latency_spike", params={"multiplier": 200}),
            FaultEvent(time_ms=38_000, target="redis", fault_type="error_injection", params={"error_rate": 0.15}),
            FaultEvent(time_ms=45_000, target="redis", fault_type="error_injection", params={"error_rate": 0.0}),
            FaultEvent(time_ms=50_000, target="user-store", fault_type="kill", params={}),
            FaultEvent(time_ms=70_000, target="db", fault_type="latency_spike", params={"multiplier": 1}),
        ],
    )


def whack_a_mole() -> ScenarioConfig:
    """Every time one fault is "fixed", another appears — 4 sequential root causes.

    Timeline:
    - 5s: redis killed
    - 8s: redis back, but payments now has errors (injected independently)
    - 15s: [RED HERRING] auth latency 5x
    - 20s: payments errors fixed, but db goes slow
    - 25s: auth latency recovers
    - 30s: [RED HERRING] user-store 10% errors
    - 40s: db fixed, but auth starts leaking memory
    - 45s: user-store errors stop
    - 60s: [RED HERRING] gateway latency 3x
    - 70s: gateway latency recovers
    - 90-150s: auth eventually OOMs, restarts, system recovers
    """
    return ScenarioConfig(
        name="whack_a_mole",
        duration_ms=150_000,
        seed=4242,
        request_rate=5.0,
        description="Sequential root causes with noise — each fix reveals a new problem",
        faults=[
            FaultEvent(time_ms=5_000, target="redis", fault_type="kill", params={}),
            FaultEvent(time_ms=8_000, target="payments", fault_type="error_injection", params={"error_rate": 0.6}),
            FaultEvent(time_ms=15_000, target="auth", fault_type="latency_spike", params={"multiplier": 5}),
            FaultEvent(time_ms=20_000, target="payments", fault_type="error_injection", params={"error_rate": 0.0}),
            FaultEvent(time_ms=20_000, target="db", fault_type="latency_spike", params={"multiplier": 150}),
            FaultEvent(time_ms=25_000, target="auth", fault_type="latency_spike", params={"multiplier": 1}),
            FaultEvent(time_ms=30_000, target="user-store", fault_type="error_injection", params={"error_rate": 0.1}),
            FaultEvent(time_ms=40_000, target="db", fault_type="latency_spike", params={"multiplier": 1}),
            FaultEvent(time_ms=40_000, target="auth", fault_type="memory_leak", params={"leak_rate_mb_per_sec": 10}),
            FaultEvent(time_ms=45_000, target="user-store", fault_type="error_injection", params={"error_rate": 0.0}),
            FaultEvent(time_ms=60_000, target="gateway", fault_type="latency_spike", params={"multiplier": 3}),
            FaultEvent(time_ms=70_000, target="gateway", fault_type="latency_spike", params={"multiplier": 1}),
        ],
    )


SCENARIOS: dict[str, callable] = {
    "deployment_cascade": deployment_cascade,
    "memory_leak": memory_leak,
    "db_latency_spike": db_latency_spike,
    "redis_failure": redis_failure,
    "traffic_surge": traffic_surge,
    "multi_fault": multi_fault,
    "rolling_restart_failure": rolling_restart_failure,
    "user_store_latency": user_store_latency,
    "cascading_kill": cascading_kill,
    "intermittent_errors": intermittent_errors,
    "payments_memory_leak": payments_memory_leak,
    "db_connection_flood": db_connection_flood,
    "auth_error_spike": auth_error_spike,
    "slow_burn_cascade": slow_burn_cascade,
    "total_meltdown": total_meltdown,
    "gateway_overload": gateway_overload,
    "redis_latency_auth_kill": redis_latency_auth_kill,
    "double_deploy_conflict": double_deploy_conflict,
    "needle_in_haystack": needle_in_haystack,
    "ghost_in_the_machine": ghost_in_the_machine,
    "blame_the_wrong_service": blame_the_wrong_service,
    "chaos_monkey": chaos_monkey,
    "the_perfect_storm": the_perfect_storm,
    "false_recovery": false_recovery,
    "whack_a_mole": whack_a_mole,
}


def get_scenario(name: str) -> ScenarioConfig:
    if name not in SCENARIOS:
        available = ", ".join(sorted(SCENARIOS.keys()))
        raise ValueError(f"Unknown scenario '{name}'. Available: {available}")
    return SCENARIOS[name]()


def list_scenarios() -> list[str]:
    return sorted(SCENARIOS.keys())
