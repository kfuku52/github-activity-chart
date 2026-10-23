"""Compare CLI startup in fresh processes, optionally against another source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

CASES = {
    "help": (["--help"], 0),
    "invalid_username": (["invalid--username"], 2),
    "invalid_range": (["benchmark", "--from", "2025-12", "--to", "2025-01"], 1),
    "invalid_output": (["benchmark", "--output", "chart.jpg"], 1),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--baseline-source", type=Path, help="Baseline src directory; alternates measurement order")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    sources = {"candidate": Path(__file__).resolve().parents[1] / "src"}
    if args.baseline_source is not None:
        if not (args.baseline_source / "githubactivitychart" / "cli.py").is_file():
            parser.error("--baseline-source must contain githubactivitychart/cli.py")
        sources = {"baseline": args.baseline_source.resolve(), **sources}

    results = {}
    for name, (arguments, expected_code) in CASES.items():
        samples = {label: {"cpu_seconds": [], "elapsed_seconds": []} for label in sources}
        expected_output = None
        for iteration in range(args.repeats + 1):
            order = list(sources) if iteration % 2 == 0 else list(reversed(sources))
            for label in order:
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(sources[label])
                before = resource.getrusage(resource.RUSAGE_CHILDREN)
                started = time.perf_counter()
                completed = subprocess.run(
                    [sys.executable, "-m", "githubactivitychart.cli", *arguments],
                    env=environment,
                    capture_output=True,
                    check=False,
                )
                elapsed = time.perf_counter() - started
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
                if completed.returncode != expected_code:
                    raise RuntimeError(f"Unexpected exit code for {label}/{name}: {completed.returncode}")
                output = {
                    "exit_code": completed.returncode,
                    "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                    "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
                }
                if expected_output is not None and output != expected_output:
                    raise RuntimeError(f"CLI output changed for {label}/{name}; comparison is invalid.")
                expected_output = output
                if iteration:
                    samples[label]["cpu_seconds"].append(cpu)
                    samples[label]["elapsed_seconds"].append(elapsed)
        for sample in samples.values():
            sample["cpu_median_seconds"] = statistics.median(sample["cpu_seconds"])
            sample["elapsed_median_seconds"] = statistics.median(sample["elapsed_seconds"])
        results[name] = {"arguments": arguments, "output": expected_output, "samples": samples}

    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "sources": {label: str(source) for label, source in sources.items()},
                "warmups": 1,
                "repeats": args.repeats,
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
