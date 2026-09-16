from __future__ import annotations

import re

TAG_RULES: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r'(?i)pool.*(exhausted|full|limit)'), ['resource_exhaustion', 'connections']),
    (re.compile(r'(?i)max.*(clients|connections).*reached'), ['resource_exhaustion', 'connections']),
    (re.compile(r'(?i)(OOM|out of memory|memory.*limit|MemoryError|OutOfMemoryError)'), ['resource_exhaustion', 'memory']),
    (re.compile(r'(?i)thread.*(starvation|pool.*full)'), ['resource_exhaustion', 'threads']),
    (re.compile(r'(?i)disk.*(full|space)'), ['resource_exhaustion', 'disk']),
    (re.compile(r'(?i)(timeout|timed?\s*out|deadline.*exceeded)'), ['timeout']),
    (re.compile(r'(?i)latency.*[0-9]{4,}'), ['high_latency']),
    (re.compile(r'(?i)upstream.*(fail|error|timeout|refused)'), ['upstream_failure']),
    (re.compile(r'(?i)connect.*refused'), ['upstream_failure']),
    (re.compile(r'(?i)connection.*reset'), ['upstream_failure']),
    (re.compile(r'(?i)(unreachable|unavailable)'), ['upstream_failure']),
    (re.compile(r'(?i)(evict|OOMKill)'), ['pod_eviction']),
    (re.compile(r'(?i)CrashLoopBackOff'), ['crash_loop']),
    (re.compile(r'(?i)(deploy|starting.*v\d|rolling.*update)'), ['deployment']),
    (re.compile(r'(?i)(config.*change|reconfigur|pool_size.*changed)'), ['config_change']),
    (re.compile(r'(?i)(health.*check|readiness|liveness)'), ['health_check']),
    (re.compile(r'(?i)(gc.*pause|garbage.*collect|G1.*pause|GC pause)'), ['gc_event']),
    (re.compile(r'(?i)(cert.*expir|TLS|SSL)'), ['cert_warning']),
    (re.compile(r'(?i)slow.*(query|log)'), ['slow_query']),
    (re.compile(r'(?i)remaining connection slots'), ['resource_exhaustion', 'connections']),
    (re.compile(r'(?i)output buffer limits'), ['resource_exhaustion', 'connections']),
]


def compute_tags(template_text: str) -> list[str]:
    tags: set[str] = set()
    for pattern, tag_list in TAG_RULES:
        if pattern.search(template_text):
            tags.update(tag_list)
    return sorted(tags)
