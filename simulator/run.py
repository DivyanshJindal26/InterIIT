from __future__ import annotations

import argparse
import sys
import time

from simulator.engine import SimulationEngine
from simulator.scenarios import get_scenario, list_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Midnight Ghost Microservice Cascade Simulator",
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        default="deployment_cascade",
        help="Scenario to run (default: deployment_cascade)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available scenarios",
    )
    parser.add_argument(
        "--output", "-o", default="output",
        help="Output directory (default: output)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Override scenario seed",
    )
    parser.add_argument(
        "--scale", type=float, default=1.0,
        help="Scale factor for request rate (e.g. --scale 1000 = 1000x more requests)",
    )
    parser.add_argument(
        "--duration", type=int, default=None,
        help="Override scenario duration in milliseconds (e.g. --duration 3600000 for 1 hour)",
    )
    parser.add_argument(
        "--pad-logs", action="store_true",
        help="Add realistic padding to logs (stack traces, request bodies, headers) for bulk",
    )
    parser.add_argument(
        "--replicas", type=int, default=1,
        help="Simulate N replicas of each pod (multiplies logs/metrics N times)",
    )
    parser.add_argument(
        "--streaming", action="store_true",
        help="Stream output to disk as it's generated instead of buffering in memory",
    )
    args = parser.parse_args()

    if args.list:
        print("Available scenarios:")
        for name in list_scenarios():
            scenario = get_scenario(name)
            print(f"  {name}: {scenario.description}")
        return

    try:
        scenario = get_scenario(args.scenario)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.seed is not None:
        scenario.seed = args.seed
    if args.duration is not None:
        scenario.duration_ms = args.duration
    if args.scale != 1.0:
        scenario.request_rate *= args.scale

    print(f"Running scenario: {scenario.name}")
    print(f"  Duration: {scenario.duration_ms}ms ({scenario.duration_ms / 1000:.0f}s)")
    print(f"  Seed: {scenario.seed}")
    print(f"  Request rate: {scenario.request_rate}/tick")
    print(f"  Faults: {len(scenario.faults)}")
    if args.scale != 1.0:
        print(f"  Scale: {args.scale}x")
    if args.replicas > 1:
        print(f"  Replicas: {args.replicas}")
    if args.pad_logs:
        print(f"  Log padding: enabled")
    if args.streaming:
        print(f"  Streaming: enabled")

    engine = SimulationEngine(
        scenario,
        pad_logs=args.pad_logs,
        replicas=args.replicas,
        streaming=args.streaming,
        output_dir=args.output if args.streaming else None,
    )
    t0 = time.monotonic()
    engine.run()
    elapsed = time.monotonic() - t0

    print(f"\nSimulation complete in {elapsed:.2f}s")
    print(f"  Spans generated: {len(engine.all_spans)}")
    print(f"  Error traces: {len(engine.error_trace_ids)}")
    print(f"  K8s events: {len(engine.k8s_events)}")

    total_logs = sum(len(v) for v in engine.log_buffers.values())
    print(f"  Log lines: {total_logs}")
    print(f"  Metric points: {len(engine.metrics_buffer)}")

    if args.streaming:
        engine.finalize_streaming()
        print(f"\nStreaming output written to {args.output}/{scenario.name}/")
    else:
        engine.write_output(args.output)
        print(f"\nOutput written to {args.output}/{scenario.name}/")


if __name__ == "__main__":
    main()
