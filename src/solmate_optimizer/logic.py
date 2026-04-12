"""Pure decision engine: electricity prices + weather + time → 24h injection profile.

Profile values are fractions 0.0–1.0 of the SolMate's max capacity (800W).
So 0.0625 = 50W, 0.125 = 100W, 0.25 = 200W, etc.
Battery state is also a fraction 0.0–1.0 (as returned by the SDK).

Decision priority:
  1. Price < 0 (negative) → never inject, grid is paying consumers to take power (0/0)
  2. Price < P25 of 24h prices → don't inject, electricity is cheap (0/0)
  3. Battery critically low → protect battery (0/50W)
  4. Price > P75 AND battery OK AND sun expected AND not nighttime → inject hard (200/400W)
  4. Price > P75 AND battery OK but no sun coming AND not nighttime → inject moderately (100/200W)
     (nighttime skips priority 4 entirely — no solar production, battery must be preserved)
  5. Middle prices, night (default 23:00–07:59, set via NIGHTTIME) → baseload (20/50W)
  5. Middle prices, daytime (default 08:00–17:59) → let PV charge (0/50W)
  5. Middle prices, evening (default 18:00–22:59) → cover household consumption (50/120W)

Price-based rules (1 and 2) always win over battery protection: even a low battery
should not inject when prices are negative or very cheap.
"""

import os
from dataclasses import dataclass

# --- Configurable parameters (via env vars, with defaults) ---

BATTERY_LOW_THRESHOLD = float(os.environ.get("BATTERY_LOW_THRESHOLD", "0.25"))
BATTERY_HIGH_THRESHOLD = float(os.environ.get("BATTERY_HIGH_THRESHOLD", "0.75"))
CLOUD_SUN_THRESHOLD = int(os.environ.get("CLOUD_SUN_THRESHOLD", "60"))
MAX_WATTS = float(os.environ.get("MAX_WATTS", "800.0"))

# NIGHTTIME="start,end": first nighttime hour (inclusive) and first morning hour (exclusive).
# The window wraps around midnight, e.g. "23,8" → hours 23, 0, 1, 2, 3, 4, 5, 6, 7.
_nt_start_str, _nt_end_str = os.environ.get("NIGHTTIME", "23,8").split(",")
NIGHTTIME_START = int(_nt_start_str)  # inclusive
NIGHTTIME_END = int(_nt_end_str)      # exclusive


@dataclass
class HourlyProfile:
    min_val: list[float]  # 24 elements, index 0 = midnight, fraction 0-1
    max_val: list[float]  # 24 elements, index 0 = midnight, fraction 0-1
    reasons: list[str]  # 24 elements, human-readable


def _frac(watts: float) -> float:
    """Convert watts to fraction of max capacity."""
    return watts / MAX_WATTS


def _quantile(values: list[float], q: float) -> float:
    """Compute quantile (0-1) of a sorted list."""
    sorted_v = sorted(values)
    idx = q * (len(sorted_v) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_v) - 1)
    frac = idx - lo
    return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac


def _is_night_hour(hour: int) -> bool:
    """Return True if *hour* falls in the configured nighttime window.

    The window wraps around midnight: NIGHTTIME_START is inclusive,
    NIGHTTIME_END is exclusive. Default "23,8" → hours 23, 0–7.
    """
    return hour >= NIGHTTIME_START or hour < NIGHTTIME_END


def _sun_expected(clouds_by_hour: dict[int, int]) -> bool:
    """Check if sun is expected in the upcoming daytime hours (8-18)."""
    daytime = [clouds_by_hour[h] for h in range(8, 18) if h in clouds_by_hour]
    if not daytime:
        return False  # no forecast → be conservative
    avg = sum(daytime) / len(daytime)
    return avg < CLOUD_SUN_THRESHOLD


def compute_profile(
    prices_by_hour: dict[int, float],
    clouds_now: int,
    clouds_by_hour: dict[int, int],
    current_hour: int,
    battery_state: float | None = None,
) -> HourlyProfile:
    """Compute a 24-hour injection profile.

    Args:
        prices_by_hour: hour (0-23) → ct/kWh. May be partial.
        clouds_now: current cloud coverage %.
        clouds_by_hour: hour (0-23) → cloud %. May be partial.
        current_hour: current hour (0-23).
        battery_state: current battery level as fraction 0.0–1.0, or None if unknown.

    Returns:
        HourlyProfile with 24-element min/max arrays (fractions 0-1) and reasons.
    """
    min_val = [0.0] * 24
    max_val = [0.0] * 24
    reasons = [""] * 24

    # Compute price quantiles from available data
    price_values = list(prices_by_hour.values()) if prices_by_hour else []
    if len(price_values) >= 4:
        p25 = _quantile(price_values, 0.25)
        p75 = _quantile(price_values, 0.75)
    else:
        # Not enough data for meaningful quantiles → disable price-based rules
        p25 = None
        p75 = None

    sun_coming = _sun_expected(clouds_by_hour)
    battery_ok = battery_state is not None and battery_state >= BATTERY_LOW_THRESHOLD

    for hour in range(24):
        price = prices_by_hour.get(hour)
        clouds = clouds_by_hour.get(hour, clouds_now)

        # --- Priority 1: Negative price → never inject (grid pays consumers to take power) ---
        if price is not None and price < 0:
            min_val[hour] = 0.0
            max_val[hour] = 0.0
            reasons[hour] = f"Negative price ({price:.1f} ct) — never inject"
            continue

        # --- Priority 2: Price below P25 → don't inject, electricity is cheap ---
        if price is not None and p25 is not None and price <= p25:
            min_val[hour] = 0.0
            max_val[hour] = 0.0
            reasons[hour] = f"Price low ({price:.1f} ct <= P25={p25:.1f} ct)"
            continue

        # --- Priority 3: Battery critically low ---
        if battery_state is not None and battery_state < BATTERY_LOW_THRESHOLD:
            min_val[hour] = 0.0
            max_val[hour] = _frac(50)
            reasons[hour] = f"Battery low ({battery_state*100:.0f}%)"
            continue

        # --- Priority 4: Price above P75 → inject hard if battery OK + sun expected ---
        # Never inject hard during nighttime: no solar production,
        # draining the battery overnight leaves nothing for daytime recharging.
        if price is not None and p75 is not None and price >= p75 and battery_ok and not _is_night_hour(hour):
            if sun_coming:
                min_val[hour] = _frac(200)
                max_val[hour] = _frac(400)
                reasons[hour] = f"Price high ({price:.1f} ct >= P75={p75:.1f} ct), battery OK, sun expected"
            else:
                # Price is high but no sun coming → inject moderately, don't drain battery
                min_val[hour] = _frac(100)
                max_val[hour] = _frac(200)
                reasons[hour] = f"Price high ({price:.1f} ct >= P75={p75:.1f} ct), no sun expected"
            continue

        # --- Priority 5: Middle prices → moderate, time-of-day based ---
        is_night = _is_night_hour(hour)
        is_evening = 18 <= hour < NIGHTTIME_START

        if is_night:
            min_val[hour] = _frac(20)
            max_val[hour] = _frac(50)
            reasons[hour] = "Night/baseload"
        elif is_evening:
            min_val[hour] = _frac(50)
            max_val[hour] = _frac(120)
            reasons[hour] = "Evening consumption"
        else:
            min_val[hour] = 0.0
            max_val[hour] = _frac(50)
            reasons[hour] = "Daytime, let PV charge"

    return HourlyProfile(min_val=min_val, max_val=max_val, reasons=reasons)
