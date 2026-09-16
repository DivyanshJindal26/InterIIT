from __future__ import annotations

from collections import defaultdict


def extract_ground_truth_targets(ground_truth: dict) -> set[str]:
    targets = set()
    for fault in ground_truth.get('faults', []):
        if fault.get('type') != 'rollback':
            targets.add(fault['target'])
    return targets


def derive_category(ground_truth: dict) -> str:
    fault_types = set()
    for fault in ground_truth.get('faults', []):
        if fault.get('type') != 'rollback':
            fault_types.add(fault['type'])

    if 'config_change' in fault_types:
        return 'deployment'
    if 'memory_leak' in fault_types:
        return 'exhaustion'
    if 'kill' in fault_types and 'latency_spike' not in fault_types and 'error_injection' not in fault_types:
        return 'kill'
    if 'latency_spike' in fault_types and len(fault_types) == 1:
        return 'latency'
    if 'error_injection' in fault_types and len(fault_types) == 1:
        return 'error_injection'
    return 'compound'


def compute_aggregate_metrics(results: list[dict]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r['correct'])
    top3 = sum(1 for r in results if r['predicted_rank'] is not None and r['predicted_rank'] <= 3)

    reciprocal_ranks = []
    for r in results:
        if r['predicted_rank'] is not None:
            reciprocal_ranks.append(1.0 / r['predicted_rank'])
        else:
            reciprocal_ranks.append(0.0)
    mrr = sum(reciprocal_ranks) / max(len(reciprocal_ranks), 1)

    categories: dict[str, dict] = defaultdict(lambda: {'correct': 0, 'total': 0})
    for r in results:
        cat = r['category']
        categories[cat]['total'] += 1
        if r['correct']:
            categories[cat]['correct'] += 1
    for cat in categories:
        t = categories[cat]['total']
        categories[cat]['accuracy'] = categories[cat]['correct'] / t if t > 0 else 0.0

    cal = {
        'high': {'total': 0, 'correct': 0},
        'medium': {'total': 0, 'correct': 0},
        'low': {'total': 0, 'correct': 0},
    }
    for r in results:
        conf = r['confidence']
        if conf >= 0.8:
            bucket = 'high'
        elif conf >= 0.5:
            bucket = 'medium'
        else:
            bucket = 'low'
        cal[bucket]['total'] += 1
        if r['correct']:
            cal[bucket]['correct'] += 1
    for bucket in cal:
        t = cal[bucket]['total']
        cal[bucket]['accuracy'] = cal[bucket]['correct'] / t if t > 0 else None

    budgets = [r['budget_used'] for r in results]
    times = [r['time_seconds'] for r in results]

    failures = [r for r in results if not r['correct']]

    return {
        'total': total,
        'top1_accuracy': correct / total if total > 0 else 0.0,
        'top1_correct': correct,
        'top3_accuracy': top3 / total if total > 0 else 0.0,
        'top3_correct': top3,
        'mrr': round(mrr, 3),
        'per_category': dict(categories),
        'confidence_calibration': cal,
        'budget': {
            'mean': round(sum(budgets) / len(budgets), 1) if budgets else 0,
            'max': max(budgets) if budgets else 0,
            'min': min(budgets) if budgets else 0,
        },
        'time': {
            'mean_seconds': round(sum(times) / len(times), 2) if times else 0,
            'max_seconds': round(max(times), 2) if times else 0,
        },
        'failures': failures,
    }
