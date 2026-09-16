from __future__ import annotations

from simulator.models import FaultEvent, ScenarioConfig, ServiceConfig


DEEP_CHAIN_SERVICES = [
    ServiceConfig(
        name="api-gateway", language="python", log_framework="stdlib",
        zone="us-east-1a", base_latency_ms=5.0, max_connections=2000,
        dependencies=["order-processor"],
        pod_name="api-gateway-x1a02", ip_address="10.1.0.1",
        edge_types={"order-processor": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="order-processor", language="java", log_framework="log4j",
        zone="us-east-1b", base_latency_ms=12.0, max_connections=500,
        dependencies=["inventory-cache"],
        pod_name="order-processor-y2b13", ip_address="10.1.0.2",
        edge_types={"inventory-cache": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="inventory-cache", language="go", log_framework="zerolog",
        zone="us-east-1a", base_latency_ms=4.0, max_connections=800,
        dependencies=["warehouse-svc"],
        pod_name="inventory-cache-z3c24", ip_address="10.1.0.3",
        edge_types={"warehouse-svc": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="warehouse-svc", language="go", log_framework="zerolog",
        zone="us-east-1c", base_latency_ms=7.0, max_connections=600,
        dependencies=["shipping-db"],
        pod_name="warehouse-svc-w4d35", ip_address="10.1.0.4",
        edge_types={"shipping-db": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="shipping-db", language="c", log_framework="postgres-native",
        zone="us-east-1b", base_latency_ms=3.0, max_connections=200,
        dependencies=[],
        pod_name="shipping-db-v5e46", ip_address="10.1.0.5",
    ),
    ServiceConfig(
        name="metrics-collector", language="python", log_framework="stdlib",
        zone="us-east-1a", base_latency_ms=3.0, max_connections=300,
        dependencies=[],
        pod_name="metrics-collector-u6f57", ip_address="10.1.0.6",
    ),
    ServiceConfig(
        name="audit-logger", language="go", log_framework="zerolog",
        zone="us-east-1c", base_latency_ms=4.0, max_connections=300,
        dependencies=[],
        pod_name="audit-logger-t7g68", ip_address="10.1.0.7",
    ),
]

DEEP_CHAIN_PATHS = [
    ["api-gateway", "order-processor", "inventory-cache", "warehouse-svc", "shipping-db"],
]

DEEP_CHAIN_ENDPOINTS = {
    "api-gateway": [("POST", "/api/v2/orders"), ("GET", "/api/v2/status"), ("POST", "/api/v2/checkout")],
    "order-processor": [("POST", "/process"), ("GET", "/order/status"), ("POST", "/validate")],
    "inventory-cache": [("GET", "/inventory/check"), ("GET", "/inventory/reserve")],
    "warehouse-svc": [("GET", "/stock/lookup"), ("POST", "/stock/allocate")],
    "shipping-db": [("SELECT", "/query"), ("INSERT", "/insert")],
    "metrics-collector": [("GET", "/scrape"), ("GET", "/health")],
    "audit-logger": [("POST", "/log"), ("GET", "/health")],
}


FANOUT_SERVICES = [
    ServiceConfig(
        name="load-balancer", language="python", log_framework="stdlib",
        zone="us-east-1a", base_latency_ms=4.0, max_connections=3000,
        dependencies=["search-index", "recommendation-engine", "notification-hub", "billing-engine"],
        pod_name="load-balancer-a1b01", ip_address="10.2.0.1",
        edge_types={
            "search-index": "SYNC_RPC",
            "recommendation-engine": "SYNC_RPC",
            "notification-hub": "SYNC_RPC",
            "billing-engine": "SYNC_RPC",
        },
    ),
    ServiceConfig(
        name="search-index", language="java", log_framework="log4j",
        zone="us-east-1b", base_latency_ms=20.0, max_connections=400,
        dependencies=[],
        pod_name="search-index-c2d12", ip_address="10.2.0.2",
    ),
    ServiceConfig(
        name="recommendation-engine", language="go", log_framework="zerolog",
        zone="us-east-1a", base_latency_ms=10.0, max_connections=500,
        dependencies=[],
        pod_name="recommendation-engine-e3f23", ip_address="10.2.0.3",
    ),
    ServiceConfig(
        name="notification-hub", language="go", log_framework="zerolog",
        zone="us-east-1c", base_latency_ms=6.0, max_connections=700,
        dependencies=[],
        pod_name="notification-hub-g4h34", ip_address="10.2.0.4",
    ),
    ServiceConfig(
        name="billing-engine", language="java", log_framework="log4j",
        zone="us-east-1b", base_latency_ms=15.0, max_connections=300,
        dependencies=[],
        pod_name="billing-engine-i5j45", ip_address="10.2.0.5",
    ),
    ServiceConfig(
        name="health-monitor", language="python", log_framework="stdlib",
        zone="us-east-1a", base_latency_ms=3.0, max_connections=300,
        dependencies=[],
        pod_name="health-monitor-k6l56", ip_address="10.2.0.6",
    ),
    ServiceConfig(
        name="log-aggregator", language="go", log_framework="zerolog",
        zone="us-east-1c", base_latency_ms=4.0, max_connections=300,
        dependencies=[],
        pod_name="log-aggregator-m7n67", ip_address="10.2.0.7",
    ),
]

FANOUT_PATHS = [
    ["load-balancer", "search-index"],
    ["load-balancer", "recommendation-engine"],
    ["load-balancer", "notification-hub"],
    ["load-balancer", "billing-engine"],
]

FANOUT_ENDPOINTS = {
    "load-balancer": [("POST", "/api/dispatch"), ("GET", "/api/health"), ("POST", "/api/route")],
    "search-index": [("POST", "/search"), ("GET", "/suggest"), ("POST", "/index")],
    "recommendation-engine": [("GET", "/recommend"), ("GET", "/similar")],
    "notification-hub": [("POST", "/notify"), ("POST", "/broadcast"), ("GET", "/status")],
    "billing-engine": [("POST", "/charge"), ("POST", "/invoice"), ("GET", "/balance")],
    "health-monitor": [("GET", "/scrape"), ("GET", "/health")],
    "log-aggregator": [("POST", "/ingest"), ("GET", "/health")],
}


DIAMOND_SERVICES = [
    ServiceConfig(
        name="cdn-proxy", language="python", log_framework="stdlib",
        zone="us-east-1a", base_latency_ms=4.0, max_connections=2500,
        dependencies=["image-processor", "video-transcoder"],
        pod_name="cdn-proxy-p1q01", ip_address="10.3.0.1",
        edge_types={"image-processor": "SYNC_RPC", "video-transcoder": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="image-processor", language="java", log_framework="log4j",
        zone="us-east-1b", base_latency_ms=25.0, max_connections=300,
        dependencies=["object-store"],
        pod_name="image-processor-r2s12", ip_address="10.3.0.2",
        edge_types={"object-store": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="video-transcoder", language="go", log_framework="zerolog",
        zone="us-east-1c", base_latency_ms=30.0, max_connections=250,
        dependencies=["object-store"],
        pod_name="video-transcoder-t3u23", ip_address="10.3.0.3",
        edge_types={"object-store": "SYNC_RPC"},
    ),
    ServiceConfig(
        name="object-store", language="c", log_framework="redis-native",
        zone="us-east-1a", base_latency_ms=2.0, max_connections=8000,
        dependencies=[],
        pod_name="object-store-v4w34", ip_address="10.3.0.4",
    ),
    ServiceConfig(
        name="cache-warmer", language="python", log_framework="stdlib",
        zone="us-east-1b", base_latency_ms=3.0, max_connections=300,
        dependencies=[],
        pod_name="cache-warmer-x5y45", ip_address="10.3.0.5",
    ),
    ServiceConfig(
        name="rate-limiter", language="go", log_framework="zerolog",
        zone="us-east-1c", base_latency_ms=2.0, max_connections=500,
        dependencies=[],
        pod_name="rate-limiter-z6a56", ip_address="10.3.0.6",
    ),
]

DIAMOND_PATHS = [
    ["cdn-proxy", "image-processor", "object-store"],
    ["cdn-proxy", "video-transcoder", "object-store"],
]

DIAMOND_ENDPOINTS = {
    "cdn-proxy": [("GET", "/asset/fetch"), ("POST", "/asset/upload"), ("GET", "/asset/status")],
    "image-processor": [("POST", "/resize"), ("POST", "/thumbnail"), ("GET", "/status")],
    "video-transcoder": [("POST", "/transcode"), ("POST", "/segment"), ("GET", "/progress")],
    "object-store": [("GET", "/GET"), ("SET", "/SET")],
    "cache-warmer": [("POST", "/warm"), ("GET", "/health")],
    "rate-limiter": [("GET", "/check"), ("GET", "/health")],
}


def novel_deep_chain_leaf_crash() -> ScenarioConfig:
    """Leaf service (shipping-db) killed in a 5-hop deep chain.

    The deepest service dies with no dependencies. The cascade
    propagates upward through warehouse-svc, inventory-cache,
    order-processor, and finally api-gateway.

    Timeline:
    - 0-15s: steady state
    - 15s: shipping-db killed (leaf node, no dependencies)
    - 15-60s: cascade propagates upward through 4 services
    - 60-90s: shipping-db restarts, system recovers
    """
    return ScenarioConfig(
        name="novel_deep_chain_leaf_crash",
        duration_ms=90_000,
        seed=10001,
        request_rate=5.0,
        description="Leaf DB killed in 5-hop deep chain — cascade propagates upward",
        services=DEEP_CHAIN_SERVICES,
        request_paths=DEEP_CHAIN_PATHS,
        http_endpoints=DEEP_CHAIN_ENDPOINTS,
        faults=[
            FaultEvent(time_ms=15_000, target="shipping-db", fault_type="kill", params={}),
        ],
    )


def novel_deep_chain_middle_leak() -> ScenarioConfig:
    """Memory leak in the middle of a 5-hop chain (inventory-cache).

    Slow leak takes ~2 minutes to cascade. The service gradually
    degrades, and its upstream callers accumulate errors.

    Timeline:
    - 0-10s: steady state
    - 10s: inventory-cache starts leaking memory (slow rate)
    - 10-120s: gradual degradation, GC pressure doesn't help (it's Go)
    - ~100-120s: inventory-cache OOMs, cascade to order-processor and api-gateway
    - 120-180s: recovery after restart
    """
    return ScenarioConfig(
        name="novel_deep_chain_middle_leak",
        duration_ms=180_000,
        seed=10002,
        request_rate=5.0,
        description="Slow memory leak in middle of 5-hop chain — 2-minute cascade",
        services=DEEP_CHAIN_SERVICES,
        request_paths=DEEP_CHAIN_PATHS,
        http_endpoints=DEEP_CHAIN_ENDPOINTS,
        faults=[
            FaultEvent(
                time_ms=10_000, target="inventory-cache", fault_type="memory_leak",
                params={"leak_rate_mb_per_sec": 8},
            ),
        ],
    )


def novel_deep_chain_head_overload() -> ScenarioConfig:
    """Entry point (api-gateway) gets bad deploy — everything downstream healthy.

    The entry point itself is the root cause. All downstream services
    are fine, but the gateway blocks all traffic.

    Timeline:
    - 0-10s: steady state
    - 10s: api-gateway bad deploy — latency 20x + 50% errors
    - 10-50s: all requests fail at the entry point
    - 50s: rollback
    - 50-90s: recovery
    """
    return ScenarioConfig(
        name="novel_deep_chain_head_overload",
        duration_ms=90_000,
        seed=10003,
        request_rate=5.0,
        description="Entry-point bad deploy in deep chain — downstream healthy",
        services=DEEP_CHAIN_SERVICES,
        request_paths=DEEP_CHAIN_PATHS,
        http_endpoints=DEEP_CHAIN_ENDPOINTS,
        faults=[
            FaultEvent(
                time_ms=10_000, target="api-gateway", fault_type="config_change",
                params={"connection_pool_size": 400, "version": "v3.1.0-bad"},
            ),
            FaultEvent(
                time_ms=10_000, target="api-gateway", fault_type="error_injection",
                params={"error_rate": 0.5},
            ),
            FaultEvent(
                time_ms=50_000, target="api-gateway", fault_type="rollback",
                params={"revert_config": True, "version": "v3.0.0"},
            ),
            FaultEvent(
                time_ms=50_000, target="api-gateway", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def novel_deep_chain_silent_killer() -> ScenarioConfig:
    """warehouse-svc crashes instantly deep in the chain — almost no logs.

    The service dies immediately and produces minimal output.
    The cascade propagates upward but the root cause service
    is nearly silent.

    Timeline:
    - 0-20s: steady state
    - 20s: warehouse-svc killed instantly (near-silent death)
    - 20-70s: cascade through inventory-cache, order-processor, api-gateway
    - 70s: warehouse-svc restarts after dead period
    - 70-120s: recovery
    """
    return ScenarioConfig(
        name="novel_deep_chain_silent_killer",
        duration_ms=120_000,
        seed=10004,
        request_rate=5.0,
        description="Near-silent crash deep in 5-hop chain — minimal logs from root cause",
        services=DEEP_CHAIN_SERVICES,
        request_paths=DEEP_CHAIN_PATHS,
        http_endpoints=DEEP_CHAIN_ENDPOINTS,
        faults=[
            FaultEvent(time_ms=20_000, target="warehouse-svc", fault_type="kill", params={}),
        ],
    )


def novel_fanout_dual_fault() -> ScenarioConfig:
    """Two simultaneous independent faults in a wide fan-out topology.

    search-index gets a latency spike AND billing-engine gets error
    injection — two completely independent root causes affecting
    different backends of the same load balancer.

    Timeline:
    - 0-10s: steady state
    - 10s: search-index latency 150x (slow queries)
    - 12s: billing-engine 60% error injection (bad code deploy)
    - 10-60s: two of four backends failing independently
    - 60s: search-index recovers
    - 65s: billing-engine errors fixed
    - 65-100s: recovery
    """
    return ScenarioConfig(
        name="novel_fanout_dual_fault",
        duration_ms=100_000,
        seed=10005,
        request_rate=5.0,
        description="Two simultaneous independent faults in wide fan-out — multi-root-cause",
        services=FANOUT_SERVICES,
        request_paths=FANOUT_PATHS,
        http_endpoints=FANOUT_ENDPOINTS,
        faults=[
            FaultEvent(
                time_ms=10_000, target="search-index", fault_type="latency_spike",
                params={"multiplier": 150},
            ),
            FaultEvent(
                time_ms=12_000, target="billing-engine", fault_type="error_injection",
                params={"error_rate": 0.6},
            ),
            FaultEvent(
                time_ms=60_000, target="search-index", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
            FaultEvent(
                time_ms=65_000, target="billing-engine", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def novel_fanout_retry_victim() -> ScenarioConfig:
    """One backend fails, retries overwhelm a healthy neighbor.

    notification-hub gets error injection (root cause).
    load-balancer becomes FAILING from notification-hub errors.
    Retries from the failing load-balancer cascade to ALL backends,
    making recommendation-engine look worse than the actual cause.

    Timeline:
    - 0-10s: steady state
    - 10s: notification-hub 70% errors (root cause)
    - 10-30s: load-balancer degrades, retries hit all backends
    - 30-60s: recommendation-engine overwhelmed by retries (victim)
    - 60s: notification-hub errors stop
    - 60-90s: recovery
    """
    return ScenarioConfig(
        name="novel_fanout_retry_victim",
        duration_ms=90_000,
        seed=10006,
        request_rate=5.0,
        description="Backend failure causes retry storm — healthy neighbor looks worse than cause",
        services=FANOUT_SERVICES,
        request_paths=FANOUT_PATHS,
        http_endpoints=FANOUT_ENDPOINTS,
        faults=[
            FaultEvent(
                time_ms=10_000, target="notification-hub", fault_type="error_injection",
                params={"error_rate": 0.7},
            ),
            FaultEvent(
                time_ms=60_000, target="notification-hub", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def novel_fanout_cascading_kills() -> ScenarioConfig:
    """Sequential kills across a fan-out — two backends killed 10s apart.

    notification-hub killed first, then search-index killed while
    notification-hub is still restarting. Tests sequential fault
    identification in a wide topology.

    Timeline:
    - 0-10s: steady state
    - 10s: notification-hub killed
    - 20s: search-index killed (notification-hub still restarting)
    - 10-35s: two of four backends down at different times
    - ~13s: notification-hub restarts
    - ~23s: search-index restarts
    - 35-70s: recovery
    """
    return ScenarioConfig(
        name="novel_fanout_cascading_kills",
        duration_ms=70_000,
        seed=10007,
        request_rate=5.0,
        description="Sequential kills in wide fan-out — two backends killed 10s apart",
        services=FANOUT_SERVICES,
        request_paths=FANOUT_PATHS,
        http_endpoints=FANOUT_ENDPOINTS,
        faults=[
            FaultEvent(time_ms=10_000, target="notification-hub", fault_type="kill", params={}),
            FaultEvent(time_ms=20_000, target="search-index", fault_type="kill", params={}),
        ],
    )


def novel_diamond_shared_dep_latency() -> ScenarioConfig:
    """Shared dependency (object-store) latency spike in diamond topology.

    Both image-processor and video-transcoder depend on object-store.
    When it gets slow, both paths degrade simultaneously.

    Timeline:
    - 0-15s: steady state
    - 15s: object-store latency 200x
    - 15-65s: both diamond paths degrade
    - 65s: object-store recovers
    - 65-100s: recovery
    """
    return ScenarioConfig(
        name="novel_diamond_shared_dep_latency",
        duration_ms=100_000,
        seed=10008,
        request_rate=5.0,
        description="Shared dependency latency spike in diamond — both paths degrade",
        services=DIAMOND_SERVICES,
        request_paths=DIAMOND_PATHS,
        http_endpoints=DIAMOND_ENDPOINTS,
        faults=[
            FaultEvent(
                time_ms=15_000, target="object-store", fault_type="latency_spike",
                params={"multiplier": 200},
            ),
            FaultEvent(
                time_ms=65_000, target="object-store", fault_type="latency_spike",
                params={"multiplier": 1},
            ),
        ],
    )


def novel_diamond_one_arm_deploy() -> ScenarioConfig:
    """Bad deploy on one arm of the diamond (image-processor).

    Only the image path degrades; video path stays healthy.
    Tests partial diamond failure identification.

    Timeline:
    - 0-10s: steady state
    - 10s: image-processor bad deploy (pool flood + latency spike + errors)
    - 10-50s: image path fails, video path healthy
    - 50s: rollback + recovery
    - 50-80s: recovery
    """
    return ScenarioConfig(
        name="novel_diamond_one_arm_deploy",
        duration_ms=80_000,
        seed=10009,
        request_rate=5.0,
        description="Bad deploy on one diamond arm — other arm stays healthy",
        services=DIAMOND_SERVICES,
        request_paths=DIAMOND_PATHS,
        http_endpoints=DIAMOND_ENDPOINTS,
        faults=[
            FaultEvent(
                time_ms=10_000, target="image-processor", fault_type="config_change",
                params={"connection_pool_size": 350, "version": "v4.2.0-hotfix"},
            ),
            FaultEvent(
                time_ms=10_000, target="image-processor", fault_type="error_injection",
                params={"error_rate": 0.4},
            ),
            FaultEvent(
                time_ms=50_000, target="image-processor", fault_type="rollback",
                params={"revert_config": True, "version": "v4.1.0"},
            ),
            FaultEvent(
                time_ms=50_000, target="image-processor", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
        ],
    )


def novel_diamond_deploy_with_noise() -> ScenarioConfig:
    """Real root cause (cdn-proxy deploy) buried under noise in diamond topology.

    cdn-proxy gets a bad config change (root cause).
    video-transcoder gets brief error injection (noise, clears quickly).
    object-store gets killed briefly (noise, auto-restarts in 3s).

    Timeline:
    - 0-8s: steady state
    - 5s: [NOISE] video-transcoder 20% errors
    - 8s: [ROOT CAUSE] cdn-proxy bad deploy (pool flood)
    - 12s: [NOISE] object-store killed briefly
    - 15s: video-transcoder errors stop
    - 15s: object-store restarts
    - 8-55s: cdn-proxy pool flood cascades through both paths
    - 55s: cdn-proxy rollback
    - 55-90s: recovery
    """
    return ScenarioConfig(
        name="novel_diamond_deploy_with_noise",
        duration_ms=90_000,
        seed=10010,
        request_rate=5.0,
        description="Entry-point deploy buried under noise in diamond topology",
        services=DIAMOND_SERVICES,
        request_paths=DIAMOND_PATHS,
        http_endpoints=DIAMOND_ENDPOINTS,
        faults=[
            FaultEvent(
                time_ms=5_000, target="video-transcoder", fault_type="error_injection",
                params={"error_rate": 0.2},
            ),
            FaultEvent(
                time_ms=8_000, target="cdn-proxy", fault_type="config_change",
                params={"connection_pool_size": 500, "version": "v2.0.0-experimental"},
            ),
            FaultEvent(
                time_ms=12_000, target="object-store", fault_type="kill",
                params={},
            ),
            FaultEvent(
                time_ms=15_000, target="video-transcoder", fault_type="error_injection",
                params={"error_rate": 0.0},
            ),
            FaultEvent(
                time_ms=55_000, target="cdn-proxy", fault_type="rollback",
                params={"revert_config": True, "version": "v1.9.0"},
            ),
        ],
    )


NOVEL_SCENARIOS: dict[str, callable] = {
    "novel_deep_chain_leaf_crash": novel_deep_chain_leaf_crash,
    "novel_deep_chain_middle_leak": novel_deep_chain_middle_leak,
    "novel_deep_chain_head_overload": novel_deep_chain_head_overload,
    "novel_deep_chain_silent_killer": novel_deep_chain_silent_killer,
    "novel_fanout_dual_fault": novel_fanout_dual_fault,
    "novel_fanout_retry_victim": novel_fanout_retry_victim,
    "novel_fanout_cascading_kills": novel_fanout_cascading_kills,
    "novel_diamond_shared_dep_latency": novel_diamond_shared_dep_latency,
    "novel_diamond_one_arm_deploy": novel_diamond_one_arm_deploy,
    "novel_diamond_deploy_with_noise": novel_diamond_deploy_with_noise,
}
