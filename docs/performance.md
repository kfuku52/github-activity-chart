# Performance review

Reviewed the API client and retries, CLI and atomic output replacement, plotting,
dependency metadata and locks, and all three GitHub Actions workflows. The main
avoidable work was network round trips and repeated figure construction.

## Changes

- Fetch up to six months per GraphQL request, keeping requests sequential. A
  monthly contribution node represents a day, so request 31 daily entries rather
  than 100. Repository and pagination caps still fail closed. See GitHub's
  [contribution schema](https://docs.github.com/en/graphql/reference/commits#commitcontributionsbyrepository)
  and [query limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api).
  Six full monthly collections request at most 18,600 daily nodes. A retry may
  repeat up to six months of work; no partial response is published.
- Build and validate one figure for multiple exports. Reset its axes geometry
  before each backend saves it, preserving the bytes produced by independent
  renders. Keep the existing single-output function and atomic installation with
  rollback. Avoid summing repository totals twice when ordering series.
- Import Matplotlib only when rendering, so help and argument validation avoid
  its startup cost.
- Generate the full-history PNG/PDF and rolling SVG from one data fetch in
  `python -m githubactivitychart.update_charts`. The previous workflow fetched
  the latest 36 months a second time and started a second CLI process. Retain
  the previous complete-month cutoff, first-contribution trimming, 36-month
  padding, filenames, public-only data and separate read/write jobs.
- Cancel superseded CI for the same branch or PR. Retain all four Python
  versions, formatting, lint, branch coverage, builds and notification behavior.

## Measurements

Baseline: `726129649f382a89df3662d85170b7ae3c9d18f2`.
Environment: macOS 27.0.1, arm64, CPython 3.12.14, Matplotlib 3.11.1,
dependencies from `requirements/dev.lock`. Measurements were made on
2026-10-15. The baseline and updated code used the same interpreter and locks.

For the live API workload (`kfuku52`, January–December 2025, public-only), warm
the connection with one month, then repeat the full range three times:

| Metric | Before | After |
| --- | ---: | ---: |
| GraphQL requests per 12 months | 12 | 2 |
| Median elapsed time | 5.873 s | 1.559 s |
| Elapsed samples | 6.247, 5.836, 5.873 s | 1.643, 1.461, 1.559 s |

This run used 83.3% fewer HTTP requests and took 73.5% less time. Public
repository counts for every month matched exactly. Network timings depend on
GitHub and connection conditions; fewer requests do not imply an equal reduction
in GraphQL rate-limit points.

The offline fixture contains 120 months, 40 repositories with 10 active in each
month, top 20 plus Other, and PNG/PDF/SVG exports. Each process performs one
warmup, then repeated measured renders and help subprocesses. It records elapsed
time, CPU time and process peak RSS. Fixture PNG, PDF and SVG SHA-256 digests
matched the baseline exactly. Tests also compare different export orders and
repeated exports of the same format against independent renders.

The final CPU-instrumented run used three measured samples after a warmup:

| Metric | Before | After |
| --- | ---: | ---: |
| Render CPU time, median | 4.439 s | 3.256 s |
| Render CPU samples | 4.439, 4.407, 4.641 s | 3.292, 3.256, 3.220 s |
| Render elapsed time, median | 13.244 s | 10.912 s |
| Help subprocess CPU time, median | 0.432 s | 0.111 s |
| Help elapsed time, median | 1.040 s | 0.300 s |
| Peak process RSS | 133.03 MiB | 130.34 MiB |

Rendering used 26.6% less CPU time in this fixture. The help subprocess used
74.3% less CPU time. These are process CPU measurements, excluding time spent
waiting for the scheduler.

Host contention caused substantial elapsed-time variation during rendering and
startup measurements, including earlier five-sample runs. Elapsed timings and
small RSS differences should not be treated as a reliable speed or memory
percentage. The deterministic work
reduction is three figure constructions to one for this fixture (two to one for
the scheduled PNG/PDF pair). Use the scripts below on an idle host to quantify
rendering and startup gains.

## Reproduction

Install the locked development environment, then run the same harness against
both source trees. Run them sequentially and use separate result directories:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements/dev.lock
uv pip install --python .venv/bin/python --no-deps --no-build-isolation -e .
baseline_dir="$(mktemp -d)"
git archive 726129649f382a89df3662d85170b7ae3c9d18f2 | tar -x -C "$baseline_dir"
PYTHONPATH="$baseline_dir/src" .venv/bin/python scripts/benchmark.py \
  --repeats 3 --output-dir /tmp/activity-chart-before > /tmp/activity-chart-before.json
.venv/bin/python scripts/benchmark.py \
  --repeats 3 --output-dir /tmp/activity-chart-after > /tmp/activity-chart-after.json
```

The live benchmark needs the normal GitHub authentication. It prints counts'
digests rather than repository names. Use a closed range and confirm identical
`counts_sha256` values before comparing times:

```bash
PYTHONPATH="$baseline_dir/src" .venv/bin/python scripts/benchmark_api.py \
  kfuku52 --from 2025-01 --to 2025-12
.venv/bin/python scripts/benchmark_api.py \
  kfuku52 --from 2025-01 --to 2025-12
```

## Delivery verification

The delivery checks are defined in `.github/workflows/ci.yml`:

```bash
python -m ruff format --check .
python -m ruff check .
python -m pytest --cov=githubactivitychart --cov-branch \
  --cov-report=term-missing --cov-fail-under=90
python -m build
```

Local test runs passed on Python 3.11.15, 3.12.14, 3.13.15 and 3.14.7:
113 tests per version, 94.64% branch-inclusive coverage, and successful sdist and
wheel builds on every version. A fresh environment installed the built wheel
and verified help, package imports, version and all three export formats.
Workflow validation uses
actionlint 1.7.12 plus parsed checks of event triggers, concurrency groups, the
Python matrix, coverage/build steps and publication permissions. Hosted event
execution is verified by CI after pushing; the monthly publishing job is not
manually dispatched as part of performance testing.
