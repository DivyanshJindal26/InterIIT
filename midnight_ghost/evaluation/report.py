from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def print_summary(aggregate: dict) -> None:
    print()
    print('=' * 60)
    print('  MIDNIGHT GHOST — EVALUATION RESULTS')
    print('=' * 60)
    print()
    print(f'  Scenarios evaluated:  {aggregate["total"]}')
    print()
    print(f'  Top-1 Accuracy:       {aggregate["top1_accuracy"]:.1%}  ({aggregate["top1_correct"]}/{aggregate["total"]})')
    print(f'  Top-3 Accuracy:       {aggregate["top3_accuracy"]:.1%}  ({aggregate["top3_correct"]}/{aggregate["total"]})')
    print(f'  MRR:                  {aggregate["mrr"]:.3f}')
    print()
    print('  Per-Category Accuracy:')
    for cat, stats in sorted(aggregate['per_category'].items()):
        print(f'    {cat:<20s} {stats["accuracy"]:.0%}  ({stats["correct"]}/{stats["total"]})')
    print()
    print('  Confidence Calibration:')
    for bucket in ('high', 'medium', 'low'):
        stats = aggregate['confidence_calibration'][bucket]
        acc = f'{stats["accuracy"]:.0%}' if stats['accuracy'] is not None else 'n/a'
        print(f'    {bucket:<10s} {acc:>6s}  ({stats["correct"]}/{stats["total"]})')
    print()
    print(f'  Budget:  mean={aggregate["budget"]["mean"]:.0f}  max={aggregate["budget"]["max"]}  min={aggregate["budget"]["min"]}')
    print(f'  Time:    mean={aggregate["time"]["mean_seconds"]:.2f}s  max={aggregate["time"]["max_seconds"]:.2f}s')
    print()

    failures = aggregate.get('failures', [])
    if failures:
        print(f'  Failures ({len(failures)}):')
        for f in failures:
            print(f'    {f["scenario"]:<30s} predicted={f["predicted"]:<15s} expected={",".join(sorted(f["expected"]))}')
    else:
        print('  No failures.')
    print()
    print('=' * 60)


def save_results(
    results: list[dict],
    aggregate: dict,
    output_dir: str,
) -> None:
    os.makedirs(output_dir, exist_ok=True)

    results_path = os.path.join(output_dir, 'results.json')
    with open(results_path, 'w') as f:
        json.dump({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'aggregate': aggregate,
            'per_scenario': results,
        }, f, indent=2, default=str)

    summary_path = os.path.join(output_dir, 'results_summary.txt')
    with open(summary_path, 'w') as fout:
        fout.write('Midnight Ghost Evaluation Summary\n')
        fout.write(f'Generated: {datetime.now(timezone.utc).isoformat()}\n')
        fout.write(f'Scenarios: {aggregate["total"]}\n\n')
        fout.write(f'Top-1 Accuracy: {aggregate["top1_accuracy"]:.1%} ({aggregate["top1_correct"]}/{aggregate["total"]})\n')
        fout.write(f'Top-3 Accuracy: {aggregate["top3_accuracy"]:.1%} ({aggregate["top3_correct"]}/{aggregate["total"]})\n')
        fout.write(f'MRR: {aggregate["mrr"]:.3f}\n\n')
        fout.write('Per-Category:\n')
        for cat, stats in sorted(aggregate['per_category'].items()):
            fout.write(f'  {cat:<20s} {stats["accuracy"]:.0%} ({stats["correct"]}/{stats["total"]})\n')
        fout.write(f'\nBudget: mean={aggregate["budget"]["mean"]:.0f} max={aggregate["budget"]["max"]} min={aggregate["budget"]["min"]}\n')
        fout.write(f'Time: mean={aggregate["time"]["mean_seconds"]:.2f}s max={aggregate["time"]["max_seconds"]:.2f}s\n')

    per_scenario_dir = os.path.join(output_dir, 'per_scenario')
    os.makedirs(per_scenario_dir, exist_ok=True)
    for r in results:
        scenario_path = os.path.join(per_scenario_dir, f'{r["scenario"]}.json')
        with open(scenario_path, 'w') as f:
            json.dump(r, f, indent=2, default=str)

    figures_dir = os.path.join(output_dir, 'figures')
    os.makedirs(figures_dir, exist_ok=True)
    _write_accuracy_csv(aggregate, os.path.join(figures_dir, 'accuracy_by_category.csv'))
    _write_calibration_csv(aggregate, os.path.join(figures_dir, 'confidence_calibration.csv'))
    _write_budget_csv(results, os.path.join(figures_dir, 'budget_per_scenario.csv'))


def _write_accuracy_csv(aggregate: dict, path: str) -> None:
    with open(path, 'w') as f:
        f.write('category,correct,total,accuracy\n')
        for cat, stats in sorted(aggregate['per_category'].items()):
            f.write(f'{cat},{stats["correct"]},{stats["total"]},{stats["accuracy"]:.4f}\n')


def _write_calibration_csv(aggregate: dict, path: str) -> None:
    with open(path, 'w') as f:
        f.write('bucket,correct,total,accuracy\n')
        for bucket in ('high', 'medium', 'low'):
            stats = aggregate['confidence_calibration'][bucket]
            acc = f'{stats["accuracy"]:.4f}' if stats['accuracy'] is not None else ''
            f.write(f'{bucket},{stats["correct"]},{stats["total"]},{acc}\n')


def _write_budget_csv(results: list[dict], path: str) -> None:
    with open(path, 'w') as f:
        f.write('scenario,budget_used,time_seconds,correct\n')
        for r in sorted(results, key=lambda x: x['scenario']):
            f.write(f'{r["scenario"]},{r["budget_used"]},{r["time_seconds"]:.2f},{r["correct"]}\n')
