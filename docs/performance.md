# Performance review

## Current release: 0.2.7

The baseline for this change is `2165012a4e44a51868d20ad2d9acf5695baf0081`
(version 0.2.6). [Raw measurements](benchmarks/0.2.7.json) include samples,
output hashes, environment, API counts and memory measurements.

### Changes

- Draw one polygon collection per repository instead of a separate Artist for
  each rectangle. Keep zero-height outlines, colors, hatches, series ordering,
  legend totals and geometry. Exclude clipped data collections from layout
  calculations. This reduces work without removing visible edges.
- Encode each output format once and copy it to additional destinations of the
  same format. Atomic staging and rollback cover render and copy failures.
- Request `contributions { totalCount }` rather than daily nodes. For commit
  contributions this is the total number of commits, as documented in the
  [GitHub schema](https://docs.github.com/en/graphql/reference/users#createdcommitcontributionconnection).
  Daily pagination is no longer needed; repository-cap checks and strict
  validation of aggregate counts remain in place. Private-name handling is
  unchanged.
- Share one retry loop across transport, HTTP and GraphQL rate-limit errors.
  The default three retries now allow at most four HTTP attempts per logical
  request. The 300-second rate-limit wait budget is cumulative per request;
  required server delays are never shortened. Headerless HTTP 429 responses use
  the secondary-rate-limit cooldown. Successful GraphQL bodies are decoded once.

### Measurements

Measured on 2026-10-15 with CPython 3.12.14, macOS 27.0.1 arm64, Matplotlib
3.11.1 and the locked development dependencies. Each rendering process used one
warmup and three measured repetitions; CPU values are medians. The fixture has
120 months, 40 repositories (10 active per month), and the top 20 plus Other.

| Metric | 0.2.6 | 0.2.7 |
| --- | ---: | ---: |
| PNG + PDF + SVG, CPU time | 2.915 s | 1.381 s |
| PNG + PDF + SVG, elapsed time | 3.317 s | 1.412 s |
| Three PNG destinations, CPU time | 4.522 s | 1.096 s |
| Three PNG destinations, elapsed time | 4.863 s | 1.164 s |
| Peak process RSS, three formats | 131.58 MiB | 104.77 MiB |
| Peak Python allocations, separate untimed render | 27.46 MiB | 4.05 MiB |
| Public API JSON bytes, January–September 2026 | 24,023 | 10,887 |
| Public API requests for that range | 2 | 2 |

The combined changes reduced rendering CPU time by 52.6%, or 75.8% when writing
three PNG destinations. These reductions overlap and must not be added together.
Peak Python allocations are measured with tracemalloc in a separate render;
process RSS is captured before tracing to exclude its overhead. CPU time is the
primary timing metric because host scheduling affects elapsed times.

The API comparison used `kfuku52`, public-only, one connection warmup and three
samples. All monthly repository counts matched exactly. JSON size fell 54.7%;
latency samples overlap (medians 2.512 s and 2.405 s), so this is not evidence of
a reliable network speedup.

A fault-injection scenario with three HTTP 503 responses followed by a GraphQL
rate-limit response previously produced 16 attempts and 448 seconds of requested
sleeps. It now stops after four attempts and seven seconds of requested sleeps.
The tests simulate sleeping rather than actually waiting. Persistent rate-limit
responses also stop before exceeding the cumulative wait budget.

### Reproduction

Use the locked development environment described below, then run both source
trees through the current harness sequentially:

```bash
baseline_dir="$(mktemp -d)"
git archive 2165012a4e44a51868d20ad2d9acf5695baf0081 | tar -x -C "$baseline_dir"
PYTHONPATH="$baseline_dir/src" .venv/bin/python scripts/benchmark.py --memory \
  --output-dir /tmp/activity-chart-before > /tmp/activity-chart-before.json
.venv/bin/python scripts/benchmark.py --memory \
  --output-dir /tmp/activity-chart-after > /tmp/activity-chart-after.json
# Repeat with --formats png png png to measure duplicate-format destinations.
PYTHONPATH="$baseline_dir/src" .venv/bin/python scripts/benchmark_api.py \
  kfuku52 --from 2026-01 --to 2026-09
.venv/bin/python scripts/benchmark_api.py \
  kfuku52 --from 2026-01 --to 2026-09
```

### Equivalence and delivery checks

Four workloads cover long histories, missing months, all-repository display,
long labels, Other aggregation and month-to-date annotation. On Matplotlib
3.11.1 and the minimum supported 3.8.0, before/after PNG pixels match exactly,
PDF pages rasterized by Poppler at 200 dpi match exactly, and all SVG bar
coordinates and resolved styles match. PDF/SVG file structure and hashes change
with collections, while repeated exports remain byte-for-byte deterministic.

The delivery entrypoint remains `.github/workflows/ci.yml`: Ruff formatting and
lint, pytest with branch coverage at least 90%, and isolated sdist/wheel builds.
All 128 tests pass on Python 3.11–3.14 with 95.16% branch-inclusive coverage.
The minimum-Matplotlib environment also passes all 62 plotting/CLI tests.

This project is a Git-installed CLI. It has no server deployment, release
publication workflow or configured GitHub deployment environment. Pushing main
updates the source used by new installations and the existing monthly public
chart workflow; installed copies must be upgraded explicitly by their owners.

## Historical measurements: 0.2.6

Reviewed the API client and retries, CLI and atomic output replacement, plotting,
dependency metadata and locks, and all three GitHub Actions workflows. The main
avoidable work was network round trips and repeated figure construction.

### Changes

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

### Measurements

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

### Reproduction

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

### Delivery verification

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
