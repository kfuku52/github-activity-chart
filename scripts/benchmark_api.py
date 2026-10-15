"""Measure a fixed public contribution range without printing repository names."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import UTC, datetime

from githubactivitychart.cli import parse_month
from githubactivitychart.github_api import GitHubClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--from", dest="start", type=parse_month, required=True)
    parser.add_argument("--to", dest="end", type=parse_month, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    client = GitHubClient.from_environment()
    execute = client.execute_graphql
    calls = 0
    response_bytes = 0
    post = client._session.post

    def measured_post(*args, **kwargs):
        nonlocal response_bytes
        response = post(*args, **kwargs)
        response_bytes += len(response.content)
        return response

    client._session.post = measured_post

    def counted_execute(query, variables):
        nonlocal calls
        calls += 1
        return execute(query, variables)

    client.execute_graphql = counted_execute
    now = datetime.now(UTC)
    client.fetch_monthly_commit_counts(args.username, args.start, args.start, now=now)
    times = []
    request_counts = []
    response_sizes = []
    digest = None
    for _ in range(args.repeats):
        calls = 0
        response_bytes = 0
        started = time.perf_counter()
        counts = client.fetch_monthly_commit_counts(args.username, args.start, args.end, now=now)
        times.append(time.perf_counter() - started)
        request_counts.append(calls)
        response_sizes.append(response_bytes)
        current_digest = hashlib.sha256(
            json.dumps({month.isoformat(): repos for month, repos in counts.items()}, sort_keys=True).encode()
        ).hexdigest()
        if digest is not None and digest != current_digest:
            raise RuntimeError("Contribution data changed between samples; results are not comparable.")
        digest = current_digest
    print(
        json.dumps(
            {
                "seconds": times,
                "median_seconds": statistics.median(times),
                "graphql_calls": request_counts,
                "response_json_bytes": response_sizes,
                "counts_sha256": digest,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
