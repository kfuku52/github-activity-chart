# github-activity-chart

[![CI](https://github.com/kfuku52/github-activity-chart/actions/workflows/ci.yml/badge.svg)](https://github.com/kfuku52/github-activity-chart/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Version](https://img.shields.io/badge/version-0.2.3-orange.svg)](src/githubactivitychart/__init__.py)

A CLI tool that plots monthly GitHub profile-eligible commit contributions as a
stacked bar chart split by repository.

## Installation

```bash
pip install "git+https://github.com/kfuku52/github-activity-chart.git"
```

For a reproducible installation, replace the default branch with a release tag or
full commit SHA. Using a virtual environment is recommended.

Use one of the following authentication methods:

- Set `GITHUB_TOKEN` or `GH_TOKEN`
- Use GitHub CLI authenticated with `gh auth login`

## Usage

Generate a PDF from the first detected commit month to the current month:

```bash
github-activity-chart octocat --output output/octocat_monthly_commits.pdf
```

Include private repositories:

```bash
github-activity-chart octocat --include-private --output output/octocat_with_private.pdf
```

Private repository names are aggregated under `Private repositories` by default.
Only use `--show-private-names` when the output and terminal are access-controlled:

```bash
github-activity-chart octocat --include-private --show-private-names \
  --output private-output/octocat_with_private_names.pdf
```

Specify a custom range:

```bash
github-activity-chart octocat --from 2025-01 --to 2025-12 --output output/octocat_2025.pdf
```

Write multiple output formats from a single GitHub data fetch:

```bash
github-activity-chart octocat --output output/octocat_monthly_commits.png --output output/octocat_monthly_commits.pdf
```

The chart displays the top 20 repositories by default and aggregates the remainder
under `Other`. Change that limit or explicitly display everything:

```bash
github-activity-chart octocat --top-repos 10 --output output/octocat_top10.pdf
github-activity-chart octocat --all-repositories --output output/octocat_all.pdf
```

For accounts with many repositories or long histories, use `--from` to reduce GitHub API calls:

```bash
github-activity-chart octocat --from 2024-01 --output output/octocat_recent.pdf
```

Progress is written to stderr during data fetching. Use `--quiet` to suppress it.
Progress messages never contain repository names.

## Example

Example output for `kfuku52` without private repositories:

```bash
github-activity-chart kfuku52 --output output/kfuku52_all_months.png
```

![Public-only example](https://raw.githubusercontent.com/kfuku52/github-activity-chart/main/output/kfuku52_all_months.png)

## Notes

- All repositories use GitHub's contribution-calendar definition, so owned and
  external repositories are counted consistently. This is not a count of every
  commit reachable from every branch.
- If the final month is the current month, the chart explicitly marks it as
  month-to-date and includes an `as of` timestamp.
- `--include-private` requires a token that can read the relevant private
  repositories and private contribution data. Classic personal access tokens need
  both `repo` and `read:user`; fine-grained tokens need equivalent repository and
  user permissions and may also require organization SSO authorization.
- Never publish private-included charts or `--show-private-names` logs to a public
  repository, public CI log, or public artifact store.
- `--output` can be specified multiple times; GitHub data is fetched once and rendered to each output path.
- Supported output formats are PDF, PNG, and SVG. Every requested output is
  rendered before any destination is replaced; a failure leaves existing outputs
  intact.
- GitHub contribution data has a maximum of 100 repositories per month. If the
  configured limit is reached, the command fails without writing a chart rather
  than silently publishing incomplete data.
