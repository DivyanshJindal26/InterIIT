from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.analysis.orchestrator import run_rca
from midnight_ghost.evaluation.metrics import (
    compute_aggregate_metrics,
    derive_category,
    extract_ground_truth_targets,
)
from midnight_ghost.evaluation.report import print_summary, save_results


def discover_scenarios(output_dir: str) -> list[str]:
    scenarios = []
    if not os.path.isdir(output_dir):
        return scenarios
    for name in sorted(os.listdir(output_dir)):
        scenario_dir = os.path.join(output_dir, name)
        gt_path = os.path.join(scenario_dir, 'ground_truth.json')
        config_path = os.path.join(scenario_dir, 'scenario_config.json')
        if os.path.isfile(gt_path) and os.path.isfile(config_path):
            scenarios.append(name)
    return scenarios


def is_ingested(scenario_dir: str) -> bool:
    index_dir = os.path.join(scenario_dir, 'indexes')
    traces_path = os.path.join(index_dir, 'traces.jsonl')
    return os.path.isfile(traces_path)


def run_ingestion(scenario_dir: str) -> None:
    from midnight_ghost.ingest.__main__ import (
        get_base_time_ns,
        load_zone_map,
        process_k8s_events,
        process_logs,
        process_metrics,
        process_traces,
    )
    from midnight_ghost.ingest.drain_wrapper import DrainProcessor
    from midnight_ghost.ingest.indexer import IndexBuilder
    from midnight_ghost.ingest.store import PartitionedWriter

    event_store_dir = os.path.join(scenario_dir, 'event_store')
    index_dir = os.path.join(scenario_dir, 'indexes')

    zone_map = load_zone_map(os.path.join(scenario_dir, 'scenario_config.json'))
    base_time_ns = get_base_time_ns(scenario_dir)
    drain = DrainProcessor()
    writer = PartitionedWriter(event_store_dir)

    process_logs(scenario_dir, writer, drain, zone_map)
    process_traces(scenario_dir, writer, zone_map)
    process_metrics(scenario_dir, writer, zone_map, base_time_ns)
    process_k8s_events(scenario_dir, writer, zone_map, base_time_ns)
    writer.close()

    indexer = IndexBuilder(event_store_dir, index_dir)
    indexer.build_all()


def evaluate_scenario(
    scenario_name: str,
    scenario_dir: str,
    verbose: bool = False,
) -> dict:
    config_path = os.path.join(scenario_dir, 'scenario_config.json')
    gt_path = os.path.join(scenario_dir, 'ground_truth.json')
    event_store = os.path.join(scenario_dir, 'event_store')
    indexes = os.path.join(scenario_dir, 'indexes')

    with open(config_path) as f:
        scenario_config = json.load(f)
    with open(gt_path) as f:
        ground_truth = json.load(f)

    gt_targets = extract_ground_truth_targets(ground_truth)
    category = derive_category(ground_truth)

    query_api = QueryAPI(event_store, indexes)

    t0 = time.monotonic()
    report = run_rca(query_api, scenario_config, scenario_dir)
    elapsed = time.monotonic() - t0

    predicted = report.root_cause.service
    confidence = report.root_cause.confidence
    correct = predicted in gt_targets

    candidates = [c.service for c in report.candidates]
    predicted_rank = None
    for i, svc in enumerate(candidates, 1):
        if svc in gt_targets:
            predicted_rank = i
            break

    result = {
        'scenario': scenario_name,
        'category': category,
        'predicted': predicted,
        'expected': sorted(gt_targets),
        'correct': correct,
        'predicted_rank': predicted_rank,
        'confidence': confidence,
        'failure_mode': report.failure_mode,
        'budget_used': report.budget_used,
        'time_seconds': round(elapsed, 2),
        'candidates': candidates[:5],
        'signal_agreement': report.signal_agreement,
    }

    if verbose:
        status = 'PASS' if correct else 'FAIL'
        print(f'  [{status}] {scenario_name:<30s} predicted={predicted:<15s} '
              f'expected={",".join(sorted(gt_targets)):<15s} '
              f'conf={confidence:.0%} budget={report.budget_used} '
              f'time={elapsed:.2f}s')

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Midnight Ghost Evaluation Harness',
    )
    parser.add_argument(
        '--scenarios', '-s', default='output',
        help='Directory containing scenario outputs (default: output)',
    )
    parser.add_argument(
        '--results', '-r', default='eval_results',
        help='Directory to write evaluation results (default: eval_results)',
    )
    parser.add_argument(
        '--skip-ingest', action='store_true',
        help='Skip ingestion even for un-ingested scenarios',
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Print per-scenario results as they run',
    )
    parser.add_argument(
        '--filter', '-f', default=None,
        help='Comma-separated list of scenario names to evaluate (default: all)',
    )
    args = parser.parse_args()

    all_scenarios = discover_scenarios(args.scenarios)
    if not all_scenarios:
        print(f'No scenarios found in {args.scenarios}/', file=sys.stderr)
        sys.exit(1)

    if args.filter:
        selected = set(args.filter.split(','))
        all_scenarios = [s for s in all_scenarios if s in selected]
        if not all_scenarios:
            print(f'No matching scenarios for filter: {args.filter}', file=sys.stderr)
            sys.exit(1)

    print(f'Evaluating {len(all_scenarios)} scenarios from {args.scenarios}/')
    if args.verbose:
        print()

    results = []
    for scenario_name in all_scenarios:
        scenario_dir = os.path.join(args.scenarios, scenario_name)

        if not is_ingested(scenario_dir):
            if args.skip_ingest:
                print(f'  SKIP {scenario_name} (not ingested)', file=sys.stderr)
                continue
            if args.verbose:
                print(f'  Ingesting {scenario_name}...')
            run_ingestion(scenario_dir)

        result = evaluate_scenario(scenario_name, scenario_dir, verbose=args.verbose)
        results.append(result)

    if not results:
        print('No scenarios evaluated.', file=sys.stderr)
        sys.exit(1)

    aggregate = compute_aggregate_metrics(results)
    print_summary(aggregate)
    save_results(results, aggregate, args.results)
    print(f'Results saved to {args.results}/')


if __name__ == '__main__':
    main()
