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

    print(f"Running scenario: {scenario.name}")
    print(f"  Duration: {scenario.duration_ms}ms")
    print(f"  Seed: {scenario.seed}")
    print(f"  Request rate: {scenario.request_rate}/tick")
    print(f"  Faults: {len(scenario.faults)}")

    engine = SimulationEngine(scenario)
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

    engine.write_output(args.output)
    print(f"\nOutput written to {args.output}/{scenario.name}/")


if __name__ == "__main__":
    main()
