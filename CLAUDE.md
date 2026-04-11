# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python script that dynamically adjusts an EET SolMate solar battery's 24-hour injection profile based on electricity prices (aWATTar), weather (OpenWeatherMap), and time of day. Run once per hour. This repo is a local test bed — the logic will eventually run as a Claude managed agent.

## Language Convention

All source code, variable names, docstrings, comments, and output must be in English.

## Commands

```bash
# Run the optimizer (one-shot, meant to be called hourly)
uv run solmate

# Install/sync dependencies
uv sync

# Run a single module for debugging
uv run python -c "from solmate_optimizer.logic import compute_profile; ..."
```

## Architecture

Two files, one data flow:

- **`src/solmate_optimizer/main.py`** — Orchestrator. Fetches data from three sources (aWATTar prices, OpenWeatherMap weather, SolMate current state), calls the decision engine, writes the result back to SolMate. Entry point via `__main__.py`.
- **`src/solmate_optimizer/logic.py`** — Pure decision engine. Takes prices (24h), cloud coverage (now + forecast), current hour → returns a 24-element min/max injection profile with reasons. No I/O, no side effects.

Data flow: `aWATTar + OpenWeatherMap + clock → logic.compute_profile() → SolMate set/apply profile`

## SolMate Injection Profiles

The SolMate stores **named** injection profiles (e.g., "Sonnig", "Schlechtwetter"). Each profile has two 24-element arrays: `min[24]` and `max[24]` where index 0 = midnight. The script creates/updates a profile (default name: `"dynamic"`) and applies it, leaving existing profiles untouched.

Profile API pattern:
1. `client.get_injection_profiles()` — returns dict with `injection_profiles` key
2. Update the dict, add/replace the profile key
3. `client.set_injection_profiles(profiles, timestamp)` — writes all profiles
4. `client.apply_injection_profile(name)` — activates it

## Decision Logic

The decision engine is price-driven. For the full priority table and watt levels see `src/solmate_optimizer/logic.py` (module docstring + inline comments) and the README decision table.

Profile values are fractions 0.0–1.0 of max capacity (e.g., 0.125 = 100W at 800W max).

**Docs sync rule:** whenever `logic.py` changes (priorities, watt levels, hour boundaries, new conditions), you **must** update both:
1. The module docstring at the top of `logic.py` — the "Decision priority" block.
2. The "Decision logic" table in `README.md`.

These three sources (code, docstring, README) must always agree.

## External APIs

- **aWATTar:** `GET https://api.awattar.at/v1/marketdata` — no auth, returns EUR/MWh (÷10 = ct/kWh)
- **OpenWeatherMap:** current + 5-day forecast — requires `OWM_API_KEY`
- **SolMate SDK:** `solmate-sdk` PyPI package, cloud connection via serial + password

## Environment Variables

See the [README](README.md#configuration) for the full list of required and optional env vars.

## Release Process (PyPI)

Releases are triggered by pushing a `v*` tag. GitHub Actions builds and publishes via OIDC trusted publishing — no API token needed.

### One-time PyPI setup (already done after first release)
1. pypi.org → Account Settings → Publishing → "Add a new pending publisher"
   - Project: `solmate-optimizer`, Owner: `haraldschilly`, Repo: `solmate-optimizer`
   - Workflow: `release.yml`, Environment: `release`
2. GitHub repo → Settings → Environments → create `release` (empty is fine)

### Per-release steps
```bash
# 1. Update CHANGELOG.md — move items from [Unreleased] into a new [X.Y.Z]
#    section with today's date, and add new compare links at the bottom.
#    Follows Keep a Changelog conventions.
# 2. Bump version in pyproject.toml (e.g. 0.1.0 → 0.2.0)
# 3. Sync lockfile
uv sync

# 4. Commit
git add CHANGELOG.md pyproject.toml uv.lock   # uv.lock is gitignored here, skip if so
git commit -m "Release v0.2.0"

# 5. Tag and push — this triggers the GitHub Actions release workflow
git tag v0.2.0
git push origin main v0.2.0
```

**CHANGELOG.md is required for every release** — no version bump without a corresponding entry. Group changes under `### Added`, `### Changed`, `### Fixed`, `### Removed` as appropriate.

The workflow (`.github/workflows/release.yml`) runs on any `v*` tag, builds with `uv build`, and publishes to PyPI.
