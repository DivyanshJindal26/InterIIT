from __future__ import annotations

from midnight_ghost.analysis.types import CausalCandidate


def _get_downstream_cone(service: str, dependency_graph: dict) -> set[str]:
    visited: set[str] = set()
    queue = [service]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        deps = dependency_graph.get(current, {})
        if isinstance(deps, dict):
            neighbors = deps.get('dependencies', [])
        elif isinstance(deps, list):
            neighbors = deps
        else:
            neighbors = []
        for neighbor in neighbors:
            queue.append(neighbor)
    return visited


def _get_ancestor_descendant_pairs(
    root: str,
    cone: set[str],
    dependency_graph: dict,
) -> list[tuple[str, str]]:
    pairs = []
    depth = {root: 0}
    queue = [root]
    while queue:
        current = queue.pop(0)
        deps = dependency_graph.get(current, {})
        if isinstance(deps, dict):
            neighbors = deps.get('dependencies', [])
        elif isinstance(deps, list):
            neighbors = deps
        else:
            neighbors = []
        for dep in neighbors:
            if dep in cone and dep not in depth:
                depth[dep] = depth[current] + 1
                queue.append(dep)
                pairs.append((current, dep))
    return pairs


def falsify_hypotheses(
    candidates: list[CausalCandidate],
    recovery_order: list[CausalCandidate],
    dependency_graph: dict,
) -> None:
    recovery_times: dict[str, int] = {}
    for c in candidates:
        if c.recovery_info and c.recovery_info.recovery_type == 'real' and c.recovery_info.recovery_time_ns:
            recovery_times[c.service] = c.recovery_info.recovery_time_ns

    RECOVERY_TOLERANCE_NS = 5 * 1_000_000_000

    for candidate in candidates:
        if candidate.recovery_info is None or candidate.recovery_info.recovery_type != 'real':
            continue

        if candidate.has_config_change:
            continue

        svc = candidate.service
        downstream = _get_downstream_cone(svc, dependency_graph)
        pairs = _get_ancestor_descendant_pairs(svc, downstream, dependency_graph)

        for ancestor, descendant in pairs:
            if ancestor in recovery_times and descendant in recovery_times:
                if recovery_times[descendant] < recovery_times[ancestor] - RECOVERY_TOLERANCE_NS:
                    candidate.falsified = True
                    candidate.falsification_reason = (
                        f'{descendant} recovered before {ancestor}, '
                        f'but {descendant} depends on {ancestor} under hypothesis '
                        f'that {svc} is root cause'
                    )
                    break
