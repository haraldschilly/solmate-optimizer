"""Pure decision engine: electricity prices + weather + time → 24h injection profile.

Profile values are fractions 0.0–1.0 of the SolMate's max capacity (800W default).
So 0.0625 = 50W, 0.125 = 100W, 0.25 = 200W, etc.
Battery state is also a fraction 0.0–1.0 (as returned by the SDK).

Injection levels (named, configurable):
  zero    =   0,   0 W  — no injection at all
  night   =  30,  80 W  — minimal baseload
  low     =   0,  50 W  — trickle / protect battery
  evening =  50, 120 W  — household consumption
  medium  = 100, 200 W  — moderate injection
  high    = 200, 400 W  — full injection

Decision priority (level assignment):
  1. Price < 0 (negative) → zero
  2. Price < P25 of 24h prices → zero
  3. Battery critically low → low
  4. Price > P75 AND battery OK AND sun expected AND not nighttime → high
  4. Price > P75 AND battery OK AND no sun AND not nighttime → medium
  4. Price > P75 AND battery OK AND evening AND battery < HIGH_THRESHOLD → medium
     (nighttime skips priority 4 entirely — no solar production, battery must be preserved)
  5. Middle prices, night → night
  5. Middle prices, daytime (nighttime_end to evening_start, default 08:00–17:59) → low
  5. Middle prices, evening (evening_start to nighttime_start, default 18:00–22:59) → evening

Price-based rules (1 and 2) always win over battery protection: even a low battery
should not inject when prices are negative or very cheap.
"""

from dataclasses import dataclass


def parse_level(value: str) -> tuple[float, float]:
    """Parse a 'min,max' watt string, e.g. '20,50' → (20.0, 50.0)."""
    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError(f"Level must be 'min,max', got '{value}'")
    return float(parts[0]), float(parts[1])


@dataclass(frozen=True)
class OptimizerConfig:
    """All tunable parameters for the decision engine."""
    battery_low_threshold: float = 0.25
    battery_high_threshold: float = 0.75
    cloud_sun_threshold: int = 60
    max_watts: float = 800.0
    nighttime_start: int = 23  # inclusive
    nighttime_end: int = 8     # exclusive
    evening_start: int = 18    # inclusive (evening runs from here to nighttime_start)

    # Named injection levels as (min_watts, max_watts) pairs
    level_night: tuple[float, float] = (30.0, 80.0)
    level_low: tuple[float, float] = (0.0, 50.0)
    level_evening: tuple[float, float] = (50.0, 120.0)
    level_medium: tuple[float, float] = (100.0, 200.0)
    level_high: tuple[float, float] = (200.0, 400.0)

    def __post_init__(self):
        if self.evening_start > self.nighttime_start:
            raise ValueError(
                f"evening_start ({self.evening_start}) must be <= nighttime_start ({self.nighttime_start})"
            )
        for name in ("night", "low", "evening", "medium", "high"):
            lo, hi = getattr(self, f"level_{name}")
            if lo < 0:
                raise ValueError(f"level_{name} min ({lo}) must be >= 0")
            if hi > self.max_watts:
                raise ValueError(f"level_{name} max ({hi}) must be <= max_watts ({self.max_watts})")
            if lo > hi:
                raise ValueError(f"level_{name} min ({lo}) must be <= max ({hi})")


@dataclass
class HourlyProfile:
    min_val: list[float]  # 24 elements, index 0 = midnight, fraction 0-1
    max_val: list[float]  # 24 elements, index 0 = midnight, fraction 0-1
    reasons: list[str]  # 24 elements, human-readable
    p25: float | None = None  # 25th percentile of prices (ct/kWh), None if <4 prices
    p75: float | None = None  # 75th percentile of prices (ct/kWh), None if <4 prices


def _quantile(values: list[float], q: float) -> float:
    """Compute quantile (0-1) of a sorted list."""
    sorted_v = sorted(values)
    idx = q * (len(sorted_v) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_v) - 1)
    frac = idx - lo
    return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac


def _is_night_hour(hour: int, config: OptimizerConfig) -> bool:
    """Return True if *hour* falls in the configured nighttime window.

    The window wraps around midnight: nighttime_start is inclusive,
    nighttime_end is exclusive. Default "23,8" → hours 23, 0–7.
    """
    return hour >= config.nighttime_start or hour < config.nighttime_end


def _sun_expected(clouds_by_hour: dict[int, int], config: OptimizerConfig) -> bool:
    """Check if sun is expected in the upcoming daytime hours (8-18)."""
    daytime = [clouds_by_hour[h] for h in range(8, 18) if h in clouds_by_hour]
    if not daytime:
        return False  # no forecast → be conservative
    avg = sum(daytime) / len(daytime)
    return avg < config.cloud_sun_threshold


def _level(level: tuple[float, float], config: OptimizerConfig) -> tuple[float, float]:
    """Convert a (min_watts, max_watts) level to fractions of max capacity."""
    return level[0] / config.max_watts, level[1] / config.max_watts


def compute_profile(
    prices_by_hour: dict[int, float],
    clouds_now: int,
    clouds_by_hour: dict[int, int],
    current_hour: int,
    battery_state: float | None = None,
    config: OptimizerConfig = OptimizerConfig(),
) -> HourlyProfile:
    """Compute a 24-hour injection profile.

    Args:
        prices_by_hour: hour (0-23) → ct/kWh. May be partial.
        clouds_now: current cloud coverage %.
        clouds_by_hour: hour (0-23) → cloud %. May be partial.
        current_hour: current hour (0-23).
        battery_state: current battery level as fraction 0.0–1.0, or None if unknown.
        config: tunable parameters (thresholds, max watts, nighttime window, levels).

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
        p25 = None
        p75 = None

    sun_coming = _sun_expected(clouds_by_hour, config)
    battery_ok = battery_state is not None and battery_state >= config.battery_low_threshold
    battery_high = battery_state is not None and battery_state >= config.battery_high_threshold

    for hour in range(24):
        price = prices_by_hour.get(hour)
        is_evening = config.evening_start <= hour < config.nighttime_start

        # --- Priority 1: Negative price → zero ---
        if price is not None and price < 0:
            min_val[hour] = 0.0
            max_val[hour] = 0.0
            reasons[hour] = f"Negative price ({price:.1f} ct) — never inject"
            continue

        # --- Priority 2: Price below P25 → zero ---
        if price is not None and p25 is not None and price <= p25:
            min_val[hour] = 0.0
            max_val[hour] = 0.0
            reasons[hour] = f"Price low ({price:.1f} ct <= P25={p25:.1f} ct)"
            continue

        # --- Priority 3: Battery critically low → low ---
        if battery_state is not None and battery_state < config.battery_low_threshold:
            lo, hi = _level(config.level_low, config)
            min_val[hour] = lo
            max_val[hour] = hi
            reasons[hour] = f"Battery low ({battery_state*100:.0f}%)"
            continue

        # --- Priority 4: Price above P75 → inject based on battery level and time ---
        if price is not None and p75 is not None and price >= p75 and battery_ok and not _is_night_hour(hour, config):
            if is_evening and not battery_high:
                lo, hi = _level(config.level_medium, config)
                min_val[hour] = lo
                max_val[hour] = hi
                reasons[hour] = (
                    f"Price high ({price:.1f} ct >= P75={p75:.1f} ct), "
                    f"evening, battery moderate ({battery_state*100:.0f}%)"
                )
            elif sun_coming:
                lo, hi = _level(config.level_high, config)
                min_val[hour] = lo
                max_val[hour] = hi
                reasons[hour] = f"Price high ({price:.1f} ct >= P75={p75:.1f} ct), battery OK, sun expected"
            else:
                lo, hi = _level(config.level_medium, config)
                min_val[hour] = lo
                max_val[hour] = hi
                reasons[hour] = f"Price high ({price:.1f} ct >= P75={p75:.1f} ct), no sun expected"
            continue

        # --- Priority 5: Middle prices → time-of-day based ---
        if _is_night_hour(hour, config):
            lo, hi = _level(config.level_night, config)
            min_val[hour] = lo
            max_val[hour] = hi
            reasons[hour] = "Night/baseload"
        elif is_evening:
            lo, hi = _level(config.level_evening, config)
            min_val[hour] = lo
            max_val[hour] = hi
            reasons[hour] = "Evening consumption"
        else:
            lo, hi = _level(config.level_low, config)
            min_val[hour] = lo
            max_val[hour] = hi
            reasons[hour] = "Daytime, let PV charge"

    return HourlyProfile(min_val=min_val, max_val=max_val, reasons=reasons, p25=p25, p75=p75)
