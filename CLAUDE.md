# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python script that dynamically adjusts an EET SolMate solar battery's 24-hour injection profile based on electricity prices (aWATTar), weather (OpenWeatherMap), and time of day. Run once per hour. This repo is a local test bed — the logic will eventually run as a Claude managed agent.

## Language Convention

All source code, variable names, docstrings, comments, and output must be in English.

## Commands

```bash
# Run the optimizer (one-shot, meant to be called hourly)
uv run python -m solmate_optimizer

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

## Decision Logic Priority (price-driven)

1. **Battery < 25%** — protect battery: 0/50W regardless
2. **Price < P25** of 24h prices — electricity cheap, don't inject (0/0)
3. **Price > P75 + battery OK + sun expected** — inject hard (100/200W); if no sun coming: cautious (30/100W)
4. **Middle prices** — time-of-day fallback: night 20/50W, daytime 0/50W, evening 50/120W

Profile values are fractions 0.0–1.0 of max capacity (e.g., 0.125 = 100W at 800W max).

## External APIs

- **aWATTar:** `GET https://api.awattar.at/v1/marketdata` — no auth, returns EUR/MWh (÷10 = ct/kWh)
- **OpenWeatherMap:** current + 5-day forecast — requires `OWM_API_KEY`
- **SolMate SDK:** `solmate-sdk` PyPI package, cloud connection via serial + password

## Environment Variables

Required:
- `SOLMATE_SERIAL` — device serial number
- `SOLMATE_PASSWORD` — device password
- `OWM_API_KEY` — OpenWeatherMap API key

Optional:
- `LOCATION_LATLON` — `lat:lon` format, e.g. `48.2:16.37` for Vienna (default)
- `SOLMATE_PROFILE_NAME` — injection profile name to create/update (default: `dynamic`)
- `BATTERY_LOW_THRESHOLD` — battery fraction below which injection is throttled (default: `0.25`)
- `CLOUD_SUN_THRESHOLD` — forecast cloud % below which "sun expected" (default: `60`)
- `MAX_WATTS` — SolMate max injection capacity in watts (default: `800`)
