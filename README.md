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
| 4 | Price above P75 + battery OK + sun expected + not nighttime | 200–400 W | Inject hard when it pays off and battery can recharge |
| 4 | Price above P75 + battery OK + no sun + not nighttime | 100–200 W | Price is high but can't recharge — be cautious |
| 5 | Middle prices, night (default 23:00–07:59) | 20–50 W | Baseload (fridge, standby); no solar production |
| 5 | Middle prices, daytime (default 08:00–17:59) | 0–50 W | Let PV charge the battery |
| 5 | Middle prices, evening (default 18:00–22:59) | 50–120 W | Cover active household consumption |

Priority 4 is intentionally skipped during nighttime: there is no solar production overnight, so injecting aggressively would drain the battery before the sun rises. The nighttime window defaults to 23:00–07:59 and is configurable via the `NIGHTTIME` environment variable.

Price-based rules (priorities 1 and 2) always win over battery protection: even a low battery should not inject when prices are negative or very cheap.

## Setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
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
| `CLOUD_SUN_THRESHOLD` | no | `60` | Forecast cloud % below which "sun expected" for recharging |
| `MAX_WATTS` | no | `800` | SolMate max injection capacity in watts |
| `NIGHTTIME` | no | `23,8` | Nighttime window as `start,end`: start hour is inclusive, end hour is exclusive. The window wraps around midnight — `23,8` means 23:00–07:59. High injection (priority 4) is blocked and baseload values apply during this window. |

## Run

```bash
export SOLMATE_SERIAL="your-serial"
export SOLMATE_PASSWORD="your-password"
export OWM_API_KEY="your-owm-key"

uv run solmate                       # run optimizer (default)
uv run solmate optimize --dry-run    # compute profile, don't write
uv run solmate optimize --no-activate  # write but don't activate
uv run status                        # read-only status view
uv run status --graph                # status with ASCII profile graphs
```

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
aWATTar: 13 hourly prices loaded
OpenWeatherMap: clouds 75%, 8h forecast
SolMate: PV=23W, inject=13W, battery=28%
  Current 'dynamic':
  max  ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒        ▒▒▒▒▓▓▓▓▓▓▓▓▒▒▒▒
  min  ░░░░░░░░░░░░░░                      ░░░░░░░░░░░░
                              *
        0     3     6     9     12    15    18    21

======================================================================
SolMate Optimizer — 2026-04-10 11:51
======================================================================
Price now: 11.0 ct/kWh (P25=10.1, P75=15.0, range: 8.6 – 22.6 ct/kWh)
Battery: 28%
Clouds now: 75%

Hourly profile 'dynamic':
  Hour  ct/kWh  Cloud   MinW   MaxW  Reason
  ----  ------  -----  -----  -----  ----------------------------------------
     0       -    75%     20     50  Night/baseload
     1       -    75%     20     50  Night/baseload
     2       -    84%     20     50  Night/baseload
     ...
*   11    11.0    29%      0     50  Daytime, let PV charge
    12     9.2    75%      0      0  Price low (9.2 ct <= P25=10.1 ct)
    ...
    18    15.0    75%     30    100  Price high (15.0 ct >= P75=15.0 ct), no sun expected
    19    20.5    75%     30    100  Price high (20.5 ct >= P75=15.0 ct), no sun expected
    20    22.6   100%     30    100  Price high (22.6 ct >= P75=15.0 ct), no sun expected
    21    16.6    75%     30    100  Price high (16.6 ct >= P75=15.0 ct), no sun expected
    22    14.4    75%     20     50  Night/baseload
    23    13.5    85%     20     50  Night/baseload

  New 'dynamic':
  max  ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒        ▒▒▒▒▓▓▓▓▓▓▓▓▒▒▒▒
  min  ░░░░░░░░░░░░░░                      ░░░░░░░░░░░░
                              *
        0     3     6     9     12    15    18    21
No change — profile 'dynamic' is already up to date.
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
