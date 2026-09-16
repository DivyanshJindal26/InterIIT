from __future__ import annotations

import argparse
import json
import os
import sys
import time

from midnight_ghost.ingest.parsers import detect_and_parse, service_from_filename
from midnight_ghost.ingest.drain_wrapper import DrainProcessor
from midnight_ghost.ingest.tagger import compute_tags
from midnight_ghost.ingest.events import (
    build_log_event,
    build_trace_event,
    build_metric_event,
    build_k8s_event,
)
from midnight_ghost.ingest.store import PartitionedWriter
from midnight_ghost.ingest.indexer import IndexBuilder


def load_zone_map(scenario_config_path: str) -> dict[str, str]:
    if not os.path.exists(scenario_config_path):
        return {}
    with open(scenario_config_path) as f:
        config = json.load(f)
    zone_map = {}
    for svc_name, svc_info in config.get('services', {}).items():
        zone_map[svc_name] = svc_info.get('zone', 'unknown')
    return zone_map


def get_base_time_ns(input_dir: str) -> int:
    """Derive a base timestamp from the first log file found."""
    logs_dir = os.path.join(input_dir, 'logs')
    if not os.path.isdir(logs_dir):
        return 1705329000 * 1_000_000_000
    for fname in sorted(os.listdir(logs_dir)):
        if fname.endswith('.log'):
            path = os.path.join(logs_dir, fname)
            svc = service_from_filename(fname)
            with open(path) as f:
                for line in f:
                    parsed = detect_and_parse(line, svc)
                    if parsed.timestamp_ns > 0:
                        return parsed.timestamp_ns
    return 1705329000 * 1_000_000_000


def process_logs(
    input_dir: str,
    writer: PartitionedWriter,
    drain: DrainProcessor,
    zone_map: dict[str, str],
) -> dict[str, int]:
    logs_dir = os.path.join(input_dir, 'logs')
    if not os.path.isdir(logs_dir):
        return {}

    counts: dict[str, int] = {}
    for fname in sorted(os.listdir(logs_dir)):
        if not fname.endswith('.log'):
            continue
        path = os.path.join(logs_dir, fname)
        service = service_from_filename(fname)
        zone = zone_map.get(service, 'unknown')
        count = 0

        with open(path) as f:
            for line in f:
                line = line.rstrip('\n\r')
                if not line:
                    continue
                parsed = detect_and_parse(line, service)
                if parsed.timestamp_ns == 0:
                    continue

                tid, template_text, params = drain.process(service, parsed.message)
                tags = compute_tags(template_text)

                event = build_log_event(parsed, zone, tid, template_text, params, tags)
                writer.write(event)
                count += 1

        counts[service] = count
    return counts


def process_traces(
    input_dir: str,
    writer: PartitionedWriter,
    zone_map: dict[str, str],
) -> int:
    traces_path = os.path.join(input_dir, 'traces', 'traces.jsonl')
    if not os.path.exists(traces_path):
        return 0

    count = 0
    with open(traces_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            span_data = json.loads(line)
            resource = span_data['resourceSpans'][0]['resource']
            svc_name = ''
            for attr in resource['attributes']:
                if attr['key'] == 'service.name':
                    svc_name = attr['value']['stringValue']
            zone = zone_map.get(svc_name, 'unknown')
            span = span_data['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
            status_code = span['status']['code']
            tags = ['error'] if 'ERROR' in status_code else []

            event = build_trace_event(span_data, zone, tags)
            writer.write(event)
            count += 1
    return count


def process_metrics(
    input_dir: str,
    writer: PartitionedWriter,
    zone_map: dict[str, str],
    base_time_ns: int,
) -> int:
    metrics_path = os.path.join(input_dir, 'metrics', 'metrics.jsonl')
    if not os.path.exists(metrics_path):
        return 0

    count = 0
    with open(metrics_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            zone = zone_map.get(data['service'], 'unknown')
            event = build_metric_event(data, zone, base_time_ns)
            writer.write(event)
            count += 1
    return count


def process_k8s_events(
    input_dir: str,
    writer: PartitionedWriter,
    zone_map: dict[str, str],
    base_time_ns: int,
) -> int:
    events_path = os.path.join(input_dir, 'k8s_events', 'events.jsonl')
    if not os.path.exists(events_path):
        return 0

    count = 0
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            zone = zone_map.get(data['service'], 'unknown')
            event = build_k8s_event(data, zone, base_time_ns)
            writer.write(event)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Midnight Ghost Ingestion Pipeline',
    )
    parser.add_argument(
        '--input', '-i', required=True,
        help='Input directory (simulator output)',
    )
    parser.add_argument(
        '--output', '-o', required=True,
        help='Output directory for partitioned event store',
    )
    parser.add_argument(
        '--index-output', required=True,
        help='Output directory for indexes',
    )
    args = parser.parse_args()

    t0 = time.monotonic()
    print(f'Ingestion pipeline starting...')
    print(f'  Input:  {args.input}')
    print(f'  Output: {args.output}')
    print(f'  Index:  {args.index_output}')

    zone_map = load_zone_map(os.path.join(args.input, 'scenario_config.json'))
    base_time_ns = get_base_time_ns(args.input)
    drain = DrainProcessor()
    writer = PartitionedWriter(args.output)

    print('\nProcessing logs...')
    log_counts = process_logs(args.input, writer, drain, zone_map)
    for svc, cnt in sorted(log_counts.items()):
        print(f'  {svc}: {cnt} events')

    print('\nProcessing traces...')
    trace_count = process_traces(args.input, writer, zone_map)
    print(f'  {trace_count} span events')

    print('\nProcessing metrics...')
    metric_count = process_metrics(args.input, writer, zone_map, base_time_ns)
    print(f'  {metric_count} metric events')

    print('\nProcessing k8s events...')
    k8s_count = process_k8s_events(args.input, writer, zone_map, base_time_ns)
    print(f'  {k8s_count} k8s events')

    writer.close()
    store_stats = writer.get_stats()

    print('\nBuilding indexes...')
    indexer = IndexBuilder(args.output, args.index_output)
    index_stats = indexer.build_all()

    elapsed = time.monotonic() - t0

    print(f'\n{"="*50}')
    print(f'Ingestion complete in {elapsed:.2f}s')
    print(f'  Total events:     {store_stats["total_events"]}')
    print(f'  Partitions:       {store_stats["partitions"]}')
    print(f'  Templates:        {index_stats["templates"]}')
    print(f'  Trace IDs:        {index_stats["traces"]}')
    print(f'  Metric series:    {index_stats["metric_series"]}')
    print(f'  Bloom filters:    {index_stats["bloom_filters"]}')
    print(f'\n  Events per service:')
    for svc, cnt in sorted(store_stats['per_service'].items()):
        print(f'    {svc}: {cnt}')


if __name__ == '__main__':
    main()
