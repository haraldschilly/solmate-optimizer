# SolMate Optimizer

Dynamically adjusts [EET SolMate](https://www.eet.energy/) solar battery injection profiles based on real-time electricity prices and weather data.

Designed to run as a one-shot script once per hour — either via cron or as a [Claude Managed Agent](https://platform.claude.com/docs/en/managed-agents/overview) on Anthropic's cloud platform.

## Data sources

### Electricity prices: aWATTar

[aWATTar Austria](https://www.awattar.at/) provides a **free public API** with hourly day-ahead electricity prices for the Austrian market (EPEX spot). No API key or registration needed.

- Endpoint: `GET https://api.awattar.at/v1/marketdata`
- Returns prices in EUR/MWh for the next ~24 hours
- Currently only the Austrian market is supported. Other hourly price providers (Tibber, ENTSO-E, etc.) could be added in the future.

### Weather: OpenWeatherMap

[OpenWeatherMap](https://openweathermap.org/) provides current weather and a 5-day/3-hour forecast. Used to determine cloud coverage (current and forecast) for Vienna.

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
6. Writes and activates a `"dynamic"` profile on the SolMate (existing profiles are preserved)

## Decision logic

The optimizer is **price-driven** — electricity prices already encode weather, demand, and time-of-day patterns:

| Condition | Injection | Reasoning |
|-----------|-----------|-----------|
| Battery < 25% | 0–50W | Protect battery regardless of price |
| Price below P25 of 24h | 0W | Electricity is cheap, save battery for expensive hours |
| Price above P75 + battery OK + sun expected | 100–200W | Inject hard when it pays off and battery can recharge |
| Price above P75 but no sun coming | 30–100W | Price is high but can't recharge — be cautious |
| Middle prices, night (0–7, 22–24) | 20–50W | Baseload (fridge, standby) |
| Middle prices, daytime (7–18) | 0–50W | Let PV charge the battery |
| Middle prices, evening (18–22) | 50–120W | Cover active household consumption |

## Setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SOLMATE_SERIAL` | yes | Your SolMate's serial number |
| `SOLMATE_PASSWORD` | yes | Your SolMate's user password |
| `OWM_API_KEY` | yes | OpenWeatherMap API key ([free tier](https://openweathermap.org/api) works) |
| `LOCATION_LATLON` | no | Latitude and longitude as `lat:lon` (default: `48.2:16.37` = Vienna) |
| `SOLMATE_PROFILE_NAME` | no | Name of the injection profile to create/update (default: `dynamic`) |

### Run

```bash
export SOLMATE_SERIAL="your-serial"
export SOLMATE_PASSWORD="your-password"
export OWM_API_KEY="your-owm-key"
# Optional: override location (default is Vienna)
# export LOCATION_LATLON="48.2:16.37"
# Optional: custom profile name (default is "dynamic")
# export SOLMATE_PROFILE_NAME="dynamic"
uv run python -m solmate_optimizer
```

Example output:

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
Profile 'dynamic' written
Profile 'dynamic' activated

Done.
```

## How injection profiles work

The SolMate stores named injection profiles, each containing two 24-element arrays:
- `min[24]` — minimum injection per hour (fraction 0.0–1.0 of 800W max)
- `max[24]` — maximum injection per hour

Index 0 = midnight, index 23 = 11 PM. The optimizer creates/updates a profile (name configurable via `SOLMATE_PROFILE_NAME`, default `"dynamic"`) and activates it, leaving your existing profiles ("Sonnig", "Schlechtwetter", etc.) untouched. You can switch back to any profile via the EET app at any time.

## Configurable parameters

All configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `BATTERY_LOW_THRESHOLD` | `0.25` | Battery fraction (0–1) below which injection is throttled |
| `CLOUD_SUN_THRESHOLD` | `60` | Forecast cloud % below which "sun expected" for recharging |
| `MAX_WATTS` | `800` | SolMate max injection capacity in watts |

## Deploying on GCP (Cloud Run + Cloud Scheduler)

Run the optimizer hourly on Google Cloud with zero infrastructure management.

### 1. Build and push the container

```bash
export PROJECT_ID="your-gcp-project"
export REGION="europe-west1"

# Build and push to Artifact Registry (or use Cloud Build)
gcloud builds submit --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/docker/solmate-optimizer
```

### 2. Deploy to Cloud Run

```bash
gcloud run deploy solmate-optimizer \
  --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/docker/solmate-optimizer \
  --region ${REGION} \
  --set-env-vars SOLMATE_SERIAL=your-serial,SOLMATE_PASSWORD=your-password,OWM_API_KEY=your-key \
  --no-allow-unauthenticated \
  --memory 256Mi \
  --timeout 120
```

For sensitive credentials, use [Secret Manager](https://cloud.google.com/run/docs/configuring/services/secrets) instead of plain env vars.

### 3. Schedule hourly runs

```bash
gcloud scheduler jobs create http solmate-optimizer-hourly \
  --location ${REGION} \
  --schedule "5 * * * *" \
  --time-zone "Europe/Vienna" \
  --uri "$(gcloud run services describe solmate-optimizer --region ${REGION} --format 'value(status.url)')" \
  --http-method POST \
  --oidc-service-account-email ${PROJECT_ID}@appspot.gserviceaccount.com
```

### Network

The container needs outbound access to:
- `sol.eet.energy:9124` (SolMate WebSocket)
- `api.awattar.at:443` (electricity prices)
- `api.openweathermap.org:443` (weather)

Cloud Run allows outbound by default — no VPC configuration needed.

## Dependencies

- [solmate-sdk](https://github.com/eet-energy/solmate-sdk) — EET SolMate WebSocket API client
- [httpx](https://www.python-httpx.org/) — HTTP client for aWATTar and OpenWeatherMap

## License

Apache 2.0 — see [LICENSE](LICENSE).
