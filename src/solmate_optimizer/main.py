"""Orchestrator: fetch electricity prices, weather, and SolMate state, then apply optimized profile."""

import datetime
import os
import sys

import click
import httpx
from solmate_sdk import SolMateAPIClient
from solmate_sdk.utils import DATETIME_FORMAT_INJECTION_PROFILES

from solmate_optimizer.logic import MAX_WATTS, HourlyProfile, compute_profile, _quantile, _sun_expected
from solmate_optimizer.plot import plot_profile

AWATTAR_URL = "https://api.awattar.at/v1/marketdata"
OWM_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OWM_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"

# Fallback injection (fraction of 800W) if APIs fail → 30W / 80W
FALLBACK_MIN = 30 / MAX_WATTS
FALLBACK_MAX = 80 / MAX_WATTS


def _next_occurrence(now: datetime.datetime) -> dict[int, datetime.datetime]:
    """For each hour 0-23, compute the next upcoming occurrence from now.

    If it's 11:00 now: hour 11-23 → today, hour 0-10 → tomorrow.
    """
    today = now.replace(minute=0, second=0, microsecond=0)
    result = {}
    for h in range(24):
        candidate = today.replace(hour=h)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        result[h] = candidate
    return result


def fetch_prices() -> dict[int, float]:
    """Fetch hourly electricity prices from aWATTar. Returns hour (0-23) → ct/kWh.

    For each hour, keeps the price closest to its next upcoming occurrence.
    E.g. at 11:00, hour 3 uses tomorrow's price (if available), hour 15 uses today's.
    """
    resp = httpx.get(AWATTAR_URL, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    now = datetime.datetime.now()
    targets = _next_occurrence(now)

    # Collect all prices keyed by (day, hour), then pick the best match per hour
    prices: dict[int, float] = {}
    best_dist: dict[int, float] = {}
    for entry in data["data"]:
        ts = datetime.datetime.fromtimestamp(entry["start_timestamp"] / 1000)
        hour = ts.hour
        price = entry["marketprice"] / 10.0  # EUR/MWh → ct/kWh
        dist = abs((ts - targets[hour]).total_seconds())
        if hour not in prices or dist < best_dist[hour]:
            prices[hour] = price
            best_dist[hour] = dist
    return prices


def fetch_weather(api_key: str, lat: float, lon: float) -> tuple[int, dict[int, int]]:
    """Fetch current clouds and hourly forecast from OpenWeatherMap.

    For each hour, keeps the forecast closest to its next upcoming occurrence.
    Returns:
        (clouds_now, clouds_by_hour) where clouds_by_hour maps hour (0-23) → cloud %.
    """
    params = {"lat": lat, "lon": lon, "appid": api_key}

    # Current weather
    resp = httpx.get(OWM_CURRENT_URL, params=params, timeout=15)
    resp.raise_for_status()
    clouds_now = resp.json()["clouds"]["all"]

    # 5-day/3h forecast
    resp = httpx.get(OWM_FORECAST_URL, params=params, timeout=15)
    resp.raise_for_status()
    forecast = resp.json()

    now = datetime.datetime.now()
    targets = _next_occurrence(now)

    clouds_by_hour: dict[int, int] = {}
    best_dist: dict[int, float] = {}
    for entry in forecast["list"]:
        ts = datetime.datetime.fromtimestamp(entry["dt"])
        hour = ts.hour
        clouds = entry["clouds"]["all"]
        dist = abs((ts - targets[hour]).total_seconds())
        if hour not in clouds_by_hour or dist < best_dist[hour]:
            clouds_by_hour[hour] = clouds
            best_dist[hour] = dist

    return clouds_now, clouds_by_hour


def connect_solmate(serial: str, password: str) -> SolMateAPIClient:
    """Connect and authenticate to SolMate cloud API."""
    client = SolMateAPIClient(serial)
    client.quickstart(password=password)
    return client




def parse_latlon(value: str) -> tuple[float, float]:
    """Parse 'lat:lon' string, e.g. '48.2:16.37'."""
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"LOCATION_LATLON must be 'lat:lon', got '{value}'")
    return float(parts[0]), float(parts[1])


def print_decision(profile: HourlyProfile, prices: dict[int, float], clouds_now: int, clouds_by_hour: dict[int, int], battery_state: float | None = None, profile_name: str = "dynamic") -> None:
    """Print price/battery/clouds info and the hourly decision table."""
    now = datetime.datetime.now()
    current_hour = now.hour

    if prices:
        price_values = list(prices.values())
        current_price = prices.get(current_hour, None)
        price_str = f"{current_price:.1f}" if current_price is not None else "n/a"
        if len(price_values) >= 4:
            p25 = _quantile(price_values, 0.25)
            p75 = _quantile(price_values, 0.75)
            print(f"Price now: {price_str} ct/kWh "
                  f"(P25={p25:.1f}, P75={p75:.1f}, "
                  f"range: {min(price_values):.1f} – {max(price_values):.1f} ct/kWh)")
        else:
            print(f"Price now: {price_str} ct/kWh "
                  f"(range: {min(price_values):.1f} – {max(price_values):.1f} ct/kWh)")
    else:
        print("Price: unavailable")

    if battery_state is not None:
        print(f"Battery: {battery_state*100:.0f}%")
    print(f"Clouds now: {clouds_now}%")
    print(f"\nHourly profile '{profile_name}':")
    print(f"  {'Hour':>4}  {'ct/kWh':>6}  {'Cloud':>5}  {'MinW':>5}  {'MaxW':>5}  Reason")
    print(f"  {'-'*4}  {'-'*6}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*40}")
    for h in range(24):
        marker = "*" if h == current_hour else " "
        min_w = profile.min_val[h] * MAX_WATTS
        max_w = profile.max_val[h] * MAX_WATTS
        price = prices.get(h)
        clouds = clouds_by_hour.get(h, clouds_now)
        price_str = f"{price:6.1f}" if price is not None else "     -"
        print(f"{marker} {h:4d}  {price_str}  {clouds:4d}%  {min_w:5.0f}  {max_w:5.0f}  {profile.reasons[h]}")
    print()


@click.command()
@click.option("--dry-run", is_flag=True, help="Compute and display profile, but don't write or activate it")
@click.option("--no-activate", is_flag=True, help="Write the profile to SolMate, but don't activate it")
def optimize(dry_run: bool, no_activate: bool):
    """Run the SolMate injection profile optimizer."""

    # --- Load config from env ---
    serial = os.environ.get("SOLMATE_SERIAL")
    password = os.environ.get("SOLMATE_PASSWORD")
    owm_key = os.environ.get("OWM_API_KEY")
    profile_name = os.environ.get("SOLMATE_PROFILE_NAME", "dynamic")

    if not serial or not password:
        print("Error: SOLMATE_SERIAL and SOLMATE_PASSWORD must be set", file=sys.stderr)
        sys.exit(1)

    # Location for weather (default: Vienna)
    latlon_str = os.environ.get("LOCATION_LATLON", "48.2:16.37")
    try:
        lat, lon = parse_latlon(latlon_str)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Header ---
    now = datetime.datetime.now()
    print(f"\n{'='*70}")
    print(f"SolMate Optimizer — {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")

    # --- Fetch external data ---
    prices: dict[int, float] = {}
    clouds_now = 50
    clouds_by_hour: dict[int, int] = {}

    try:
        prices = fetch_prices()
        print(f"aWATTar: {len(prices)} hourly prices loaded")
    except Exception as e:
        print(f"aWATTar error: {e} — using fallback profile", file=sys.stderr)

    if owm_key:
        try:
            clouds_now, clouds_by_hour = fetch_weather(owm_key, lat, lon)
            print(f"OpenWeatherMap: clouds {clouds_now}%, {len(clouds_by_hour)}h forecast")
        except Exception as e:
            print(f"OpenWeatherMap error: {e} — using fallback clouds", file=sys.stderr)
    else:
        print("OWM_API_KEY not set — using fallback clouds 50%", file=sys.stderr)

    # --- Connect to SolMate ---
    try:
        client = connect_solmate(serial, password)
    except Exception as e:
        print(f"SolMate connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Fetch live values (battery state) ---
    battery_state: float | None = None
    try:
        live = client.get_live_values()
        pv = live.get("pv_power", 0)
        inject = live.get("inject_power", 0)
        battery_state = live.get("battery_state")
        bat_str = f"{battery_state*100:.0f}%" if battery_state is not None else "?"
        print(f"SolMate: PV={pv:.0f}W, inject={inject:.0f}W, battery={bat_str}")
    except Exception as e:
        print(f"Failed to read live values: {e}", file=sys.stderr)

    # --- Read existing profiles ---
    try:
        settings = client.get_injection_profiles()
        existing_profiles = settings["injection_profiles"]
    except Exception as e:
        print(f"Failed to read profiles: {e}", file=sys.stderr)
        sys.exit(1)

    current_hour = datetime.datetime.now().hour

    # --- Compute profile ---
    profile = compute_profile(prices, clouds_now, clouds_by_hour, current_hour, battery_state)

    # If both APIs failed, use safe fallback
    if not prices and not clouds_by_hour:
        print("Both APIs failed — using safe fallback profile")
        profile.min_val = [FALLBACK_MIN] * 24
        profile.max_val = [FALLBACK_MAX] * 24
        profile.reasons = ["Fallback: no data available"] * 24

    # --- Check if profile actually changed ---
    changed = True
    old_profile = existing_profiles.get(profile_name)
    if old_profile is not None:
        if old_profile["min"] == profile.min_val and old_profile["max"] == profile.max_val:
            changed = False

    # --- Print decision ---
    print_decision(profile, prices, clouds_now, clouds_by_hour, battery_state, profile_name)

    # --- Plots ---
    if changed and old_profile is not None:
        plot_profile(f"Before '{profile_name}'", old_profile["min"], old_profile["max"], current_hour)
        plot_profile(f"After '{profile_name}'", profile.min_val, profile.max_val, current_hour)
    else:
        plot_profile(f"Profile '{profile_name}'", profile.min_val, profile.max_val, current_hour)

    if dry_run:
        if changed:
            print("Dry run — profile CHANGED but not written (--dry-run).")
        else:
            print("Dry run — no change needed.")
        return

    if not changed:
        print(f"No change — profile '{profile_name}' is already up to date.")
        return

    # --- Write updated profile ---
    existing_profiles[profile_name] = {
        "min": profile.min_val,
        "max": profile.max_val,
    }

    timestamp = datetime.datetime.now().strftime(DATETIME_FORMAT_INJECTION_PROFILES)
    try:
        client.set_injection_profiles(existing_profiles, timestamp)
        print(f"UPDATED — profile '{profile_name}' written")
    except Exception as e:
        print(f"Failed to write profile: {e}", file=sys.stderr)
        sys.exit(1)

    if no_activate:
        print(f"Profile '{profile_name}' written but not activated (--no-activate).")
        return

    # --- Activate profile ---
    try:
        client.apply_injection_profile(profile_name)
        print(f"Profile '{profile_name}' activated")
    except Exception as e:
        print(f"Failed to activate profile: {e}", file=sys.stderr)
        sys.exit(1)

    print("\nDone.")
