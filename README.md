# SolMate Optimizer

[![PyPI version](https://img.shields.io/pypi/v/solmate-optimizer)](https://pypi.org/project/solmate-optimizer/)
[![CI](https://github.com/haraldschilly/solmate-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/haraldschilly/solmate-optimizer/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/python/required-version-toml?tomlFilePath=https://raw.githubusercontent.com/haraldschilly/solmate-optimizer/main/pyproject.toml)](https://pypi.org/project/solmate-optimizer/)
[![License](https://img.shields.io/pypi/l/solmate-optimizer)](https://github.com/haraldschilly/solmate-optimizer/blob/main/LICENSE)

Dynamically adjusts [EET SolMate](https://www.eet.energy/) solar battery injection profiles based on real-time electricity prices and weather data.

Run as a one-shot script once per hour — locally via cron, on [GCP Cloud Run](DEPLOYMENT.md), or any other scheduler.

## Data sources

### Electricity prices: aWATTar

[aWATTar Austria](https://www.awattar.at/) provides a **free public API** with hourly day-ahead electricity prices for the Austrian market (EPEX spot). No API key or registration needed.

- Endpoint: `GET https://api.awattar.at/v1/marketdata`
- Returns prices in EUR/MWh for the next ~24 hours
- Currently only the Austrian market is supported. Other hourly price providers (Tibber, ENTSO-E, etc.) could be added in the future.

### Weather: OpenWeatherMap

[OpenWeatherMap](https://openweathermap.org/) provides current weather and a 5-day/3-hour forecast. Used to determine cloud coverage (current and forecast).

- **You need a free API key** — sign up at [openweathermap.org/api](https://openweathermap.org/api), the free tier is sufficient (current weather + 5-day forecast)
- The forecast is used to decide whether the battery can recharge via solar (sun expected vs. persistent overcast)

### SolMate: solmate-sdk

The [solmate-sdk](https://github.com/eet-energy/solmate-sdk) connects to your EET SolMate via WebSocket (cloud API). Used to read battery state, read/write injection profiles, and activate the optimized profile.

## What it does

Every run:

1. Fetches hourly electricity prices from aWATTar (public API, no auth)
2. Fetches current weather and forecast from OpenWeatherMap (free API key)
3. Connects to your SolMate via solmate-sdk (cloud API, serial + password)
4. Reads the current battery state and existing injection profiles
5. Computes an optimized 24-hour injection profile based on price quantiles, weather, and time of day
6. Compares with the current profile — only writes if something changed
7. Writes and activates the profile on the SolMate (existing profiles are preserved)

## Decision logic

The optimizer is **price-driven** — electricity prices already encode weather, demand, and time-of-day patterns.

### Injection levels

Named power levels (all configurable via env / CLI):

| Level | Default | Used for |
|-------|---------|----------|
| **zero** | 0 / 0 W | No injection (negative or cheap prices) |
| **night** | 20 / 50 W | Nighttime baseload |
| **low** | 0 / 50 W | Battery protection, daytime PV charging |
| **evening** | 50 / 120 W | Household evening consumption |
| **medium** | 100 / 200 W | High price, no sun / evening moderate battery |
| **high** | 200 / 400 W | High price, sun expected |

### Priority table

| Priority | Condition | Level | Reasoning |
|----------|-----------|-------|-----------|
| 1 | Price < 0 (negative) | zero | Grid pays consumers to take power — never inject |
| 2 | Price below P25 of 24 h | zero | Electricity is cheap, save battery for when it matters |
| 3 | Battery < 25 % | low | Protect battery regardless of price |
| 4 | Price above P75 + battery OK + sun expected + not nighttime | high | Inject hard when it pays off and battery can recharge |
| 4 | Price above P75 + battery OK + no sun + not nighttime | medium | Price is high but can't recharge — be cautious |
| 4 | Price above P75 + battery 25–75 % + evening | medium | High price but below high threshold — moderate injection, spread over time |
| 5 | Middle prices, night (default 23:00–07:59) | night | Baseload (fridge, standby); no solar production |
| 5 | Middle prices, daytime (default 08:00–17:59) | low | Let PV charge the battery |
| 5 | Middle prices, evening (default 18:00–22:59) | evening | Cover active household consumption |

Priority 4 is intentionally skipped during nighttime: there is no solar production overnight, so injecting aggressively would drain the battery before the sun rises. The nighttime window defaults to 23:00–07:59 and is configurable via `--nighttime` / `NIGHTTIME`. The evening start defaults to 18:00 and is configurable via `--evening-start` / `EVENING_START`.

During **evening hours** priority 4 distinguishes two battery bands. If the battery is at or above `BATTERY_HIGH_THRESHOLD` (default 75 %), the full power level applies. If the battery is between `BATTERY_LOW_THRESHOLD` (25 %) and `BATTERY_HIGH_THRESHOLD` (75 %), the **medium** level is used — the high price still warrants more than pure baseload, but without solar recharging available it makes sense not to drain the battery too aggressively.

Price-based rules (priorities 1 and 2) always win over battery protection: even a low battery should not inject when prices are negative or very cheap.

## Install

Requires Python 3.13+.

### Option A — run without installing (recommended for quick use)

With [uv](https://docs.astral.sh/uv/) installed, run the latest version directly — no clone, no virtualenv:

```bash
uvx --from solmate-optimizer@latest solmate      # run optimizer
uvx --from solmate-optimizer@latest status       # read-only status view
```

The `--from` flag is required because the package name (`solmate-optimizer`) differs from the executable names (`solmate`, `status`). `uvx` fetches the package from PyPI into an ephemeral environment on first run and caches it for later invocations. Pin to a specific version with e.g. `solmate-optimizer@0.2.0`.

### Option B — install via pip

```bash
pip install solmate-optimizer
solmate           # run optimizer
status            # read-only status view
```

### Option C — from source (for development)

```bash
git clone https://github.com/haraldschilly/solmate-optimizer.git
cd solmate-optimizer
uv sync
uv run solmate
```

## Configuration

All configuration is via environment variables and/or CLI options. CLI options override environment variables.

| Env variable | CLI option | Default | Description |
|-------------|-----------|---------|-------------|
| `SOLMATE_SERIAL` | — | — | Your SolMate's serial number (required) |
| `SOLMATE_PASSWORD` | — | — | Your SolMate's user password (required) |
| `OWM_API_KEY` | — | — | OpenWeatherMap API key ([free tier](https://openweathermap.org/api) works) |
| `LOCATION_LATLON` | — | `48.2:16.37` | Latitude and longitude as `lat:lon` (default: Vienna) |
| `TIMEZONE` | — | `Europe/Vienna` | Timezone for price/weather hour matching and display (use IANA names, e.g. `Europe/Berlin`) |
| `SOLMATE_PROFILE_NAME` | — | `dynamic` | Name of the injection profile to create/update |
| `BATTERY_LOW_THRESHOLD` | `--battery-low` | `0.25` | Battery fraction (0–1) below which injection is throttled |
| `BATTERY_HIGH_THRESHOLD` | `--battery-high` | `0.75` | Battery fraction (0–1) required for high-price injection during evening hours |
| `CLOUD_SUN_THRESHOLD` | `--cloud-sun-threshold` | `60` | Forecast cloud % below which "sun expected" for recharging |
| `MAX_WATTS` | `--max-watts` | `800` | SolMate max injection capacity in watts |
| `NIGHTTIME` | `--nighttime` | `23,8` | Nighttime window as `start,end` (inclusive start, exclusive end, wraps midnight) |
| `EVENING_START` | `--evening-start` | `18` | First evening hour (inclusive). Evening runs from here to nighttime start. |
| `LEVEL_NIGHT` | `--level-night` | `20,50` | Night/baseload injection level as `min,max` watts |
| `LEVEL_LOW` | `--level-low` | `0,50` | Low injection level as `min,max` watts (battery protection, daytime) |
| `LEVEL_EVENING` | `--level-evening` | `50,120` | Evening consumption injection level as `min,max` watts |
| `LEVEL_MEDIUM` | `--level-medium` | `100,200` | Medium injection level as `min,max` watts (high price, no sun) |
| `LEVEL_HIGH` | `--level-high` | `200,400` | High injection level as `min,max` watts (high price, sun expected) |

Level values are validated: min must be ≤ max, min ≥ 0, max ≤ `MAX_WATTS`, and `EVENING_START` ≤ nighttime start.

## Run

Set the required credentials in the environment, then invoke either the installed entry point (Option A/B above) or `uv run` when working from source (Option C):

```bash
export SOLMATE_SERIAL="your-serial"
export SOLMATE_PASSWORD="your-password"
export OWM_API_KEY="your-owm-key"

solmate                         # run optimizer (default)
solmate optimize --dry-run      # compute profile, don't write
solmate optimize --no-activate  # write but don't activate
status                          # read-only status view
status --graph                  # status with plotext profile graphs
```

When using `uvx`, prefix every command with `uvx --from solmate-optimizer@latest` (e.g. `uvx --from solmate-optimizer@latest solmate optimize --dry-run`). When working from a checkout, prefix with `uv run`.

### Commands

| Command | Description |
|---------|-------------|
| `solmate` | Run the optimizer (default, no subcommand needed) |
| `solmate optimize` | Explicit optimizer subcommand |
| `solmate optimize --dry-run` | Compute and display profile, but don't write or activate it |
| `solmate optimize --no-activate` | Write the profile to SolMate, but don't activate it |
| `status` | Show live values and injection profiles (read-only, no OWM/aWATTar needed) |
| `status --graph` | Same, with ASCII art visualization of each profile |
| `status --max-watts 600` | Override max watts for display (also via `MAX_WATTS` env) |

### Example output

```
======================================================================
SolMate Optimizer — 2026-04-13 18:21 CEST
======================================================================
aWATTar: 24 hourly prices loaded
OpenWeatherMap: clouds 0%, 24h forecast
SolMate: PV=16W, inject=95W, battery=97%
Price now: 15.1 ct/kWh (P25=12.0, P75=14.7, range: 11.5 – 16.5 ct/kWh)
Battery: 97%
Clouds now: 0%

Hourly profile 'dynamic':
  Hour  ct/kWh  Cloud   MinW   MaxW  Reason
  ----  ------  -----  -----  -----  ----------------------------------------
     0    12.0    97%     20     50  Night/baseload
     1    11.9    97%      0      0  Price low (11.9 ct <= P25=12.0 ct)
     2    12.0    97%      0      0  Price low (12.0 ct <= P25=12.0 ct)
     3    11.8    98%      0      0  Price low (11.8 ct <= P25=12.0 ct)
     4    12.0    99%     20     50  Night/baseload
     5    12.5   100%     20     50  Night/baseload
     6    14.7   100%     20     50  Night/baseload
     7    16.3   100%     20     50  Night/baseload
     8    16.0   100%    100    200  Price high (16.0 ct >= P75=14.7 ct), no sun expected
     9    14.5   100%      0     50  Daytime, let PV charge
    10    12.6    99%      0     50  Daytime, let PV charge
    11    12.9    99%      0     50  Daytime, let PV charge
    12    11.9    98%      0      0  Price low (11.9 ct <= P25=12.0 ct)
    13    11.5    96%      0      0  Price low (11.5 ct <= P25=12.0 ct)
    14    11.9    95%      0      0  Price low (11.9 ct <= P25=12.0 ct)
    15    12.6    97%      0     50  Daytime, let PV charge
    16    13.5    98%      0     50  Daytime, let PV charge
    17    14.8   100%    100    200  Price high (14.8 ct >= P75=14.7 ct), no sun expected
*   18    15.1    78%    100    200  Price high (15.1 ct >= P75=14.7 ct), no sun expected
    19    16.5    55%    100    200  Price high (16.5 ct >= P75=14.7 ct), no sun expected
    20    15.8    33%    100    200  Price high (15.8 ct >= P75=14.7 ct), no sun expected
    21    13.8    43%     50    120  Evening consumption
    22    13.3    53%     50    120  Evening consumption
    23    12.3    63%     20     50  Night/baseload

                                Profile 'dynamic'                             
   ┌────────────────────────────────────────────────────────┬────────────────┐
400┤                                                        │                │
   │                                                        │                │
200┤                         ▖                           ▗▄▄▄▄▄▄▄▄▄▖         │
   │                        ▞▝▖                         ▗▘  │      ▝▚▖       │
   │                       ▞▄▚▝▚                       ▗▘▞▀▀▀▀▀▀▀▀▀▚▄▝▀▀▀▀▄▖ │
  0┤▚▄▄▄▄▄▄▄▄▄▄▄▞▀▀▀▀▀▀▀▀▀▀▘  ▝▚▀▀▀▀▀▀▀▄▄▄▄▄▄▄▄▄▄▄▄▞▀▀▀▘▘   │        ▀▀▀▀▀▀▝▀│
   └┬────────┬─────────┬────────┬─────────┬────────┬────────┴─────────┬──────┘
    0        3         6        9        12       15       18        21       
```

## How injection profiles work

The SolMate stores named injection profiles, each containing two 24-element arrays:
- `min[24]` — minimum injection per hour (fraction 0.0–1.0 of 800W max)
- `max[24]` — maximum injection per hour

Index 0 = midnight, index 23 = 11 PM. The optimizer creates/updates a profile (name configurable via `SOLMATE_PROFILE_NAME`, default `"dynamic"`) and activates it, leaving your existing profiles ("Sonnig", "Schlechtwetter", etc.) untouched. You can switch back to any profile via the EET app at any time.

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for instructions on running this on GCP Cloud Run with Cloud Scheduler (hourly cron).

## Dependencies

- [solmate-sdk](https://github.com/eet-energy/solmate-sdk) — EET SolMate WebSocket API client
- [httpx](https://www.python-httpx.org/) — HTTP client for aWATTar and OpenWeatherMap

## License

Apache 2.0 — see [LICENSE](LICENSE).
