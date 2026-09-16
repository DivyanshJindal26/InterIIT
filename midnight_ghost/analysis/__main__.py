from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from midnight_ghost.query.api import QueryAPI
from midnight_ghost.analysis.orchestrator import run_rca


def main():
    parser = argparse.ArgumentParser(description='Run RCA analysis pipeline')
    parser.add_argument('--event-store', required=True, help='Path to event store directory')
    parser.add_argument('--indexes', required=True, help='Path to indexes directory')
    parser.add_argument('--config', required=True, help='Path to scenario_config.json')
    parser.add_argument('--output', default=None, help='Output directory for results')
    parser.add_argument('--ground-truth', default=None, help='Path to ground_truth.json for comparison')
    args = parser.parse_args()

    with open(args.config) as f:
        scenario_config = json.load(f)

    output_path = os.path.dirname(args.config)

    query_api = QueryAPI(args.event_store, args.indexes)

    report = run_rca(query_api, scenario_config, output_path)

    report_dict = _serialize_report(report)

    if args.output:
        os.makedirs(args.output, exist_ok=True)

        with open(os.path.join(args.output, 'incident_report.json'), 'w') as f:
            json.dump(report_dict, f, indent=2, default=str)

        summary = _build_summary(report)
        with open(os.path.join(args.output, 'summary.txt'), 'w') as f:
            f.write(summary)

        with open(os.path.join(args.output, 'budget_log.json'), 'w') as f:
            json.dump(report.budget_log, f, indent=2, default=str)

        print(f'Results written to {args.output}/')

    print()
    print(_build_summary(report))

    if args.ground_truth:
        with open(args.ground_truth) as f:
            gt = json.load(f)
        _evaluate(report, gt)


def _serialize_report(report) -> dict:
    d = asdict(report)
    return d


def _build_summary(report) -> str:
    rc = report.root_cause
    lines = [
        f'Root Cause: {rc.service}',
        f'Failure Mode: {rc.failure_mode}',
        f'Confidence: {rc.confidence:.0%}',
        f'Propagation: {" -> ".join(rc.propagation_path)}',
        f'Remediation: {report.remediation.action_type} on {report.remediation.target_service} (safe={report.remediation.safe})',
        f'Validation: {report.validation.status} (anomaly {report.validation.anomaly_before:.2f} -> {report.validation.anomaly_after:.2f})',
        f'Budget: {report.budget_used} queries',
    ]
    if rc.evidence:
        lines.append(f'Evidence ({len(rc.evidence)} samples):')
        for e in rc.evidence[:3]:
            lines.append(f'  {e[:120]}')
    return '\n'.join(lines)


def _evaluate(report, ground_truth):
    gt_faults = ground_truth.get('faults', [])
    gt_services = set()
    for fault in gt_faults:
        if fault.get('type') != 'rollback':
            gt_services.add(fault['target'])

    identified = report.root_cause.service
    correct = identified in gt_services

    print()
    print('=== Evaluation ===')
    print(f'Ground truth root cause(s): {", ".join(sorted(gt_services))}')
    print(f'Identified root cause: {identified}')
    print(f'Correct: {"YES" if correct else "NO"}')


if __name__ == '__main__':
    main()
