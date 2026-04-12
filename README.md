# SolMate Optimizer

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

The optimizer is **price-driven** — electricity prices already encode weather, demand, and time-of-day patterns:

| Priority | Condition | Injection | Reasoning |
|----------|-----------|-----------|-----------|
| 1 | Price < 0 (negative) | 0 W | Grid pays consumers to take power — never inject |
| 2 | Price below P25 of 24 h | 0 W | Electricity is cheap, save battery for when it matters |
| 3 | Battery < 25 % | 0–50 W | Protect battery regardless of price |
| 4 | Price above P75 + battery OK + sun expected + daytime | 200–400 W | Inject hard; battery recharges from solar |
| 4 | Price above P75 + battery ≥ 75 % + sun expected + evening | 200–400 W | Inject hard; battery well-charged, worth it |
| 4 | Price above P75 + battery OK + no sun + daytime | 100–200 W | Price is high but can't recharge — be cautious |
| 4 | Price above P75 + battery ≥ 75 % + no sun + evening | 100–200 W | High price, battery high enough to spread load |
| 5 | Middle prices, night (default 23:00–07:59) | 20–50 W | Baseload (fridge, standby); no solar production |
| 5 | Middle prices, daytime (default 08:00–17:59) | 0–50 W | Let PV charge the battery |
| 5 | Middle prices, evening (default 18:00–22:59) | 50–120 W | Cover active household consumption |
| 5 | High price, evening, battery < 75 % | 50–120 W | Battery not well-charged — spread over time, don't drain all at once |

Priority 4 is intentionally skipped during nighttime: there is no solar production overnight, so injecting aggressively would drain the battery before the sun rises. The nighttime window defaults to 23:00–07:59 and is configurable via the `NIGHTTIME` environment variable.

During **evening hours** (18:00–22:59) priority 4 additionally requires the battery to be at or above `BATTERY_HIGH_THRESHOLD` (default 75 %). Without incoming solar, a half-empty battery would be depleted quickly; it is better to spread the load over several hours at the lower evening rate.

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

All configuration is via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SOLMATE_SERIAL` | yes | — | Your SolMate's serial number |
| `SOLMATE_PASSWORD` | yes | — | Your SolMate's user password |
| `OWM_API_KEY` | yes | — | OpenWeatherMap API key ([free tier](https://openweathermap.org/api) works) |
| `LOCATION_LATLON` | no | `48.2:16.37` | Latitude and longitude as `lat:lon` (default: Vienna) |
| `TIMEZONE` | no | `Europe/Vienna` | Timezone for price/weather hour matching and display (use IANA names, e.g. `Europe/Berlin`) |
| `SOLMATE_PROFILE_NAME` | no | `dynamic` | Name of the injection profile to create/update |
| `BATTERY_LOW_THRESHOLD` | no | `0.25` | Battery fraction (0–1) below which injection is throttled |
| `BATTERY_HIGH_THRESHOLD` | no | `0.75` | Battery fraction (0–1) required for high-price injection during evening hours (no solar recharging possible) |
| `CLOUD_SUN_THRESHOLD` | no | `60` | Forecast cloud % below which "sun expected" for recharging |
| `MAX_WATTS` | no | `800` | SolMate max injection capacity in watts |
| `NIGHTTIME` | no | `23,8` | Nighttime window as `start,end`: start hour is inclusive, end hour is exclusive. The window wraps around midnight — `23,8` means 23:00–07:59. High injection (priority 4) is blocked and baseload values apply during this window. |

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

### Example output

```
======================================================================
SolMate Optimizer — 2026-04-11 18:27 CEST
======================================================================
aWATTar: 24 hourly prices loaded
OpenWeatherMap: clouds 0%, 8h forecast
SolMate: PV=17W, inject=83W, battery=97%
Price now: 8.4 ct/kWh (P25=3.6, P75=10.4, range: -0.0 – 11.2 ct/kWh)
Battery: 97%
Clouds now: 0%

Hourly profile 'dynamic':
  Hour  ct/kWh  Cloud   MinW   MaxW  Reason
  ----  ------  -----  -----  -----  ----------------------------------------
     0    10.2     0%     20     50  Night/baseload
     1     9.5     0%     20     50  Night/baseload
     2    10.2    24%     20     50  Night/baseload
     3     9.9     0%     20     50  Night/baseload
     4    10.7     0%     20     50  Night/baseload
     5    11.1    40%     20     50  Night/baseload
     6    10.9     0%     20     50  Night/baseload
     7    10.3     0%     20     50  Night/baseload
     8     6.4    53%      0     50  Daytime, let PV charge
     9     4.1     0%      0     50  Daytime, let PV charge
    10     2.1     0%      0      0  Price low (2.1 ct <= P25=3.6 ct)
    11     1.4    36%      0      0  Price low (1.4 ct <= P25=3.6 ct)
    12     0.1     0%      0      0  Price low (0.1 ct <= P25=3.6 ct)
    13    -0.0     0%      0      0  Negative price (-0.0 ct) — never inject
    14     0.0    51%      0      0  Price low (0.0 ct <= P25=3.6 ct)
    15     1.6     0%      0      0  Price low (1.6 ct <= P25=3.6 ct)
    16     5.8     0%      0     50  Daytime, let PV charge
    17     9.7    81%      0     50  Daytime, let PV charge
*   18     8.4     0%     50    120  Evening consumption
    19    11.2     0%    200    400  Price high (11.2 ct >= P75=10.4 ct), battery OK, sun expected
    20    11.0    14%    200    400  Price high (11.0 ct >= P75=10.4 ct), battery OK, sun expected
    21     9.9     0%     50    120  Evening consumption
    22    10.7     0%    200    400  Price high (10.7 ct >= P75=10.4 ct), battery OK, sun expected
    23    10.4    13%     20     50  Night/baseload

                                 Profile 'dynamic'
     ┌───────────────────────────────────────────────────────┬───────────────┐
460.0┤                                                       │  ▄▄▄▄     ▗   │
383.3┤                                                       │ ▞    ▚   ▗▀▖  │
230.0┤                                                       │▞ ▄▄▄▄ ▚ ▗▘▗▝▖ │
153.3┤                                                       ▞▄▀    ▀▄▚▗▞▘▚▖▖│
  0.0┤▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▀        ▀▘   ▝▄│
     └┬────────┬────────┬────────┬─────────┬────────┬────────┴────────┬──────┘
      0        3        6        9        12       15       18       21
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
