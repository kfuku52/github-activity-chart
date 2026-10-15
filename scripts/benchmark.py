"""Offline, repeatable rendering and CLI startup benchmark (no GitHub token needed)."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import resource
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from githubactivitychart.cli import render_outputs_atomically


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--formats", nargs="+", choices=("png", "pdf", "svg"), default=["png", "pdf", "svg"])
    parser.add_argument("--memory", action="store_true", help="Measure Python allocations in a separate untimed render")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    counts = {}
    for index in range(120):
        year, month = divmod(2016 * 12 + index, 12)
        counts[date(year, month + 1, 1)] = {
            f"owner/repository-{repository:02d}": (index * 7 + repository * 11) % 100 + 1
            for repository in range(40)
            if (index + repository) % 4 == 0
        }
    repeated_formats = len(set(args.formats)) != len(args.formats)
    outputs = [
        args.output_dir / (f"chart-{index}.{suffix}" if repeated_formats else f"chart.{suffix}")
        for index, suffix in enumerate(args.formats)
    ]
    render_times = []
    render_cpu_times = []
    for iteration in range(args.repeats + 1):
        gc.collect()
        started = time.perf_counter()
        cpu_started = time.process_time()
        render_outputs_atomically(
            monthly_counts=counts,
            username="benchmark",
            output_paths=outputs,
            top_repos=20,
            as_of=None,
        )
        elapsed = time.perf_counter() - started
        cpu_elapsed = time.process_time() - cpu_started
        if iteration:
            render_times.append(elapsed)
            render_cpu_times.append(cpu_elapsed)

    help_times = []
    help_cpu_times = []
    for iteration in range(args.repeats + 1):
        started = time.perf_counter()
        child_started = resource.getrusage(resource.RUSAGE_CHILDREN)
        subprocess.run(
            [sys.executable, "-m", "githubactivitychart.cli", "--help"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        if iteration:
            help_times.append(time.perf_counter() - started)
            child_finished = resource.getrusage(resource.RUSAGE_CHILDREN)
            help_cpu_times.append(
                child_finished.ru_utime + child_finished.ru_stime - child_started.ru_utime - child_started.ru_stime
            )

    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_python_bytes = None
    if args.memory:
        import tracemalloc

        gc.collect()
        tracemalloc.start()
        try:
            render_outputs_atomically(
                monthly_counts=counts,
                username="benchmark",
                output_paths=outputs,
                top_repos=20,
                as_of=None,
            )
            _, peak_python_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "workload": f"120 months, 40 repositories (10 active/month), top 20, {'+'.join(args.formats).upper()}",
                "formats": args.formats,
                "render_seconds": render_times,
                "render_median_seconds": statistics.median(render_times),
                "render_cpu_seconds": render_cpu_times,
                "render_cpu_median_seconds": statistics.median(render_cpu_times),
                "help_seconds": help_times,
                "help_median_seconds": statistics.median(help_times),
                "help_cpu_seconds": help_cpu_times,
                "help_cpu_median_seconds": statistics.median(help_cpu_times),
                "peak_rss_mib": peak_rss / (1024 * 1024 if sys.platform == "darwin" else 1024),
                "peak_python_allocations_mib": None if peak_python_bytes is None else peak_python_bytes / 1024**2,
                "output_sha256": {path.suffix: hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs},
                "output_files_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
