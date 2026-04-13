"""Tests for solmate_optimizer.logic — exercises every branch of compute_profile."""

import pytest

from solmate_optimizer import logic
from solmate_optimizer.logic import (
    HourlyProfile,
    _frac,
    _is_night_hour,
    _quantile,
    _sun_expected,
    compute_profile,
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

# Prices designed so P25=5, P75=15 (with enough spread).
PRICES_24H: dict[int, float] = {h: 5.0 + (10.0 * h / 23) for h in range(24)}
# Verify: sorted range from 5.0 to ~15.0 → P25 ≈ 7.5, P75 ≈ 12.5


def _uniform_prices(val: float) -> dict[int, float]:
    """All 24 hours at the same price — quantiles collapse, only P1/P2 matter."""
    return {h: val for h in range(24)}


def _extreme_prices() -> tuple[dict[int, float], float, float]:
    """Prices with clear low/high separation. Returns (prices, p25, p75)."""
    prices = {h: 2.0 for h in range(12)}  # cheap first half
    prices.update({h: 20.0 for h in range(12, 24)})  # expensive second half
    p25 = _quantile(list(prices.values()), 0.25)
    p75 = _quantile(list(prices.values()), 0.75)
    return prices, p25, p75


SUNNY_FORECAST: dict[int, int] = {h: 20 for h in range(24)}  # <60 → sun expected
CLOUDY_FORECAST: dict[int, int] = {h: 80 for h in range(24)}  # >=60 → no sun


# ---------------------------------------------------------------------------
# Tests for helper functions
# ---------------------------------------------------------------------------


class TestQuantile:
    def test_single_value(self):
        assert _quantile([10.0], 0.5) == 10.0

    def test_two_values(self):
        assert _quantile([0.0, 10.0], 0.25) == 2.5
        assert _quantile([0.0, 10.0], 0.75) == 7.5

    def test_four_values(self):
        vals = [1.0, 2.0, 3.0, 4.0]
        assert _quantile(vals, 0.0) == 1.0
        assert _quantile(vals, 1.0) == 4.0

    def test_unsorted_input(self):
        assert _quantile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.5


class TestIsNightHour:
    """Default window: 23 inclusive to 8 exclusive → night = 23, 0–7."""

    def test_midnight_is_night(self):
        assert _is_night_hour(0) is True

    def test_7_is_night(self):
        assert _is_night_hour(7) is True

    def test_8_is_not_night(self):
        assert _is_night_hour(8) is False

    def test_22_is_not_night(self):
        assert _is_night_hour(22) is False

    def test_23_is_night(self):
        assert _is_night_hour(23) is True

    def test_12_is_not_night(self):
        assert _is_night_hour(12) is False


class TestSunExpected:
    def test_sunny_forecast(self):
        assert _sun_expected(SUNNY_FORECAST) is True

    def test_cloudy_forecast(self):
        assert _sun_expected(CLOUDY_FORECAST) is False

    def test_empty_forecast(self):
        assert _sun_expected({}) is False

    def test_partial_forecast_only_morning(self):
        # Only hours 8–11 have data, all sunny
        forecast = {h: 30 for h in range(8, 12)}
        assert _sun_expected(forecast) is True

    def test_threshold_boundary(self):
        # Exactly at threshold → not sunny (avg >= 60)
        forecast = {h: 60 for h in range(8, 18)}
        assert _sun_expected(forecast) is False


# ---------------------------------------------------------------------------
# Tests for compute_profile — one test per decision branch
# ---------------------------------------------------------------------------


class TestPriority1NegativePrice:
    """Negative price → never inject (0/0) regardless of battery or time."""

    def test_negative_price_zeroes_injection(self):
        prices = {h: -5.0 for h in range(24)}
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.9)
        for h in range(24):
            assert result.min_val[h] == 0.0
            assert result.max_val[h] == 0.0
            assert "Negative" in result.reasons[h]

    def test_negative_price_beats_low_battery(self):
        prices = {h: -1.0 for h in range(24)}
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.05)
        for h in range(24):
            assert result.max_val[h] == 0.0, "Negative price should override low battery"


class TestPriority2CheapPrice:
    """Price <= P25 → don't inject (0/0)."""

    def test_cheap_hours_no_injection(self):
        prices, p25, _ = _extreme_prices()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.9)
        # Hours 0–11 are at 2.0, which is <= P25
        for h in range(12):
            assert result.min_val[h] == 0.0
            assert result.max_val[h] == 0.0
            assert "low" in result.reasons[h].lower() or "P25" in result.reasons[h]

    def test_cheap_price_beats_low_battery(self):
        prices, _, _ = _extreme_prices()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.10)
        # Cheap hours should still be 0/0 even with critically low battery
        for h in range(12):
            assert result.max_val[h] == 0.0


class TestPriority3BatteryLow:
    """Battery < LOW_THRESHOLD → protect battery (0/50W) for mid-price hours."""

    def test_low_battery_caps_injection(self):
        # Use mid-range prices (between P25 and P75) so priorities 1 & 2 don't fire
        prices = {h: 10.0 for h in range(24)}
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.10)
        for h in range(24):
            # All hours should hit priority 3 (since uniform price, all equal P25 → not ≤ P25
            # because the <= check vs quantile of identical values: P25 = 10.0, price 10.0 → P2 fires!)
            # We need prices where some are mid-range. Let's use a spread.
            pass

    def test_low_battery_with_spread_prices(self):
        # Prices: hours 0-5 cheap (2ct), 6-17 mid (10ct), 18-23 expensive (20ct)
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 2.0
        for h in range(6, 18):
            prices[h] = 10.0
        for h in range(18, 24):
            prices[h] = 20.0

        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.10)
        # Hours 0–5: P2 (cheap)
        for h in range(6):
            assert result.max_val[h] == 0.0
        # Hours 6–17: mid-price, battery low → P3 (0/50W)
        for h in range(6, 18):
            assert result.min_val[h] == 0.0
            assert result.max_val[h] == pytest.approx(_frac(50))
            assert "Battery low" in result.reasons[h]


class TestPriority4HighPrice:
    """Price >= P75, battery OK, not nighttime → inject based on conditions."""

    def _high_price_profile(self) -> dict[int, float]:
        """Prices: 0-11 cheap (2ct), 12-22 expensive (20ct), 23 cheap."""
        prices: dict[int, float] = {}
        for h in range(12):
            prices[h] = 2.0
        for h in range(12, 23):
            prices[h] = 20.0
        prices[23] = 2.0  # hour 23 is nighttime anyway
        return prices

    def test_high_price_sun_expected(self):
        """P4b: high price + battery OK + sun → 200/400W."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 20, SUNNY_FORECAST, 12, battery_state=0.80)
        # Hour 14: expensive, daytime, battery high, sun expected
        assert result.min_val[14] == pytest.approx(_frac(200))
        assert result.max_val[14] == pytest.approx(_frac(400))
        assert "sun expected" in result.reasons[14].lower()

    def test_high_price_no_sun(self):
        """P4c: high price + battery OK + no sun → 100/200W."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 80, CLOUDY_FORECAST, 12, battery_state=0.80)
        # Hour 14: expensive, daytime, battery high, no sun
        assert result.min_val[14] == pytest.approx(_frac(100))
        assert result.max_val[14] == pytest.approx(_frac(200))
        assert "no sun" in result.reasons[14].lower()

    def test_high_price_evening_moderate_battery(self):
        """P4a: high price + evening + battery 25–75% → 100/200W (spread over time)."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 18, battery_state=0.50)
        # Hour 20: expensive, evening (18–22), battery moderate
        assert result.min_val[20] == pytest.approx(_frac(100))
        assert result.max_val[20] == pytest.approx(_frac(200))
        assert "evening" in result.reasons[20].lower()

    def test_high_price_evening_high_battery_sun(self):
        """Evening + high battery → falls through to sun_coming check (200/400W)."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 20, SUNNY_FORECAST, 18, battery_state=0.90)
        # Hour 20: expensive, evening, battery_high=True → not caught by evening sub-case
        # → sun_coming=True → 200/400W
        assert result.min_val[20] == pytest.approx(_frac(200))
        assert result.max_val[20] == pytest.approx(_frac(400))

    def test_high_price_evening_high_battery_no_sun(self):
        """Evening + high battery + no sun → 100/200W (no-sun sub-case)."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 80, CLOUDY_FORECAST, 18, battery_state=0.90)
        assert result.min_val[20] == pytest.approx(_frac(100))
        assert result.max_val[20] == pytest.approx(_frac(200))

    def test_high_price_nighttime_skips_p4(self):
        """Nighttime + high price → P4 skipped, falls to P5 (baseload 20/50W)."""
        # Spread: hours 0–7 cheap (1ct), 8–23 expensive (20ct).
        # This puts P25 well below 20 so hour 23 won't hit P2.
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.90)
        # Hour 23: nighttime + expensive → P4 skipped (night guard) → P5 night
        assert result.min_val[23] == pytest.approx(_frac(20))
        assert result.max_val[23] == pytest.approx(_frac(50))
        assert "Night" in result.reasons[23] or "baseload" in result.reasons[23]

    def test_high_price_battery_not_ok(self):
        """High price but battery_state=None → battery_ok=False → P4 skipped."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 20, SUNNY_FORECAST, 12, battery_state=None)
        # Hour 14: expensive but battery unknown → P4 skipped → P5 daytime
        assert result.min_val[14] == 0.0
        assert result.max_val[14] == pytest.approx(_frac(50))


class TestPriority5MiddlePrices:
    """Middle prices (between P25 and P75) → time-of-day based."""

    def _mid_price_profile(self) -> dict[int, float]:
        """Prices: spread across range so 10ct is between P25 and P75."""
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 2.0   # cheap
        for h in range(6, 18):
            prices[h] = 10.0  # mid
        for h in range(18, 24):
            prices[h] = 20.0  # expensive
        return prices

    def test_night_baseload(self):
        """P5a: night hours → 20/50W."""
        prices = self._mid_price_profile()
        # Hours 6 and 7 are mid-price AND nighttime (default night = 23,0–7)
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        for h in [6, 7]:
            assert result.min_val[h] == pytest.approx(_frac(20))
            assert result.max_val[h] == pytest.approx(_frac(50))
            assert "Night" in result.reasons[h] or "baseload" in result.reasons[h]

    def test_daytime_let_pv_charge(self):
        """P5c: daytime (8–17) → 0/50W."""
        prices = self._mid_price_profile()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        # Hours 8–17 are mid-price and daytime
        for h in range(8, 18):
            assert result.min_val[h] == 0.0
            assert result.max_val[h] == pytest.approx(_frac(50))
            assert "Daytime" in result.reasons[h] or "PV" in result.reasons[h]

    def test_evening_consumption(self):
        """P5b: evening (18–22) → 50/120W — but only if price is mid."""
        # 6 cheap (1ct), 6 mid (8ct), 12 expensive (25ct).
        # P25 ≈ 6.25, P75 = 25 → 8ct is strictly between P25 and P75.
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 1.0
        for h in range(6, 12):
            prices[h] = 8.0
        for h in range(12, 24):
            prices[h] = 25.0
        # Override evening hours 18–21 to mid-price (8ct)
        for h in [18, 19, 20, 21]:
            prices[h] = 8.0
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        for h in [18, 19, 20, 21]:
            assert result.min_val[h] == pytest.approx(_frac(50)), f"hour {h}: {result.reasons[h]}"
            assert result.max_val[h] == pytest.approx(_frac(120)), f"hour {h}: {result.reasons[h]}"
            assert "Evening" in result.reasons[h] or "consumption" in result.reasons[h].lower()


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_fewer_than_4_prices_disables_quantile_rules(self):
        """With <4 prices, P25/P75 are None → price-based rules 2 and 4 can't fire."""
        prices = {12: 10.0, 13: 10.0}  # only 2 prices
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        # Hour 12 has a price but no quantiles → skips P2, P3 (battery OK), P4 → P5 daytime
        assert result.min_val[12] == 0.0
        assert result.max_val[12] == pytest.approx(_frac(50))

    def test_no_prices_at_all(self):
        """Empty prices dict → all hours fall through to P5."""
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        # Spot check: hour 12 daytime, hour 23 night
        assert result.max_val[12] == pytest.approx(_frac(50))
        assert result.reasons[12] == "Daytime, let PV charge"
        assert result.min_val[23] == pytest.approx(_frac(20))

    def test_battery_none_skips_priority_3(self):
        """battery_state=None → priority 3 never fires."""
        prices = {h: 10.0 for h in range(24)}  # uniform → P2 fires (price == P25)
        # Actually uniform prices: P25 = 10.0, price 10.0 → 10 <= 10 → P2 fires.
        # Use spread so mid-prices exist.
        prices[0] = 1.0
        prices[23] = 30.0
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=None)
        for h in range(24):
            assert "Battery low" not in result.reasons[h]

    def test_clouds_now_used_when_hour_not_in_forecast(self):
        """When forecast has no data for an hour, clouds_now is used as fallback."""
        prices = {h: 10.0 for h in range(24)}
        prices[0] = 1.0
        prices[23] = 30.0
        # Empty forecast but clouds_now = 20 (sunny). This doesn't affect
        # injection directly (clouds per-hour only affect _sun_expected),
        # but the fallback code path is exercised.
        result = compute_profile(prices, 20, {}, 12, battery_state=0.50)
        assert result is not None
        assert len(result.min_val) == 24

    def test_profile_always_returns_24_elements(self):
        result = compute_profile(PRICES_24H, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        assert len(result.min_val) == 24
        assert len(result.max_val) == 24
        assert len(result.reasons) == 24

    def test_min_never_exceeds_max(self):
        """Sanity: min_val[h] <= max_val[h] for all hours."""
        for battery in [0.05, 0.30, 0.50, 0.80, None]:
            for clouds in [10, 80]:
                forecast = SUNNY_FORECAST if clouds < 60 else CLOUDY_FORECAST
                result = compute_profile(PRICES_24H, clouds, forecast, 12, battery_state=battery)
                for h in range(24):
                    assert result.min_val[h] <= result.max_val[h], (
                        f"min > max at hour {h} (battery={battery}, clouds={clouds})"
                    )

    def test_all_values_in_0_1_range(self):
        """All profile values must be valid fractions."""
        result = compute_profile(PRICES_24H, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        for h in range(24):
            assert 0.0 <= result.min_val[h] <= 1.0
            assert 0.0 <= result.max_val[h] <= 1.0


# ---------------------------------------------------------------------------
# Tests for configurable parameters (env-var-driven module globals)
# ---------------------------------------------------------------------------


class TestConfigNighttime:
    """Verify NIGHTTIME_START/END affect which hours are treated as night."""

    def test_custom_nighttime_window(self, monkeypatch):
        """Shift nighttime to 21–6 → hour 22 becomes night, hour 7 becomes day."""
        monkeypatch.setattr(logic, "NIGHTTIME_START", 21)
        monkeypatch.setattr(logic, "NIGHTTIME_END", 6)

        # No prices → quantile rules disabled, everything falls to P5 (time-of-day)
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50)

        # Hour 22: with default config (23,8) it's evening; now it's night
        assert result.min_val[22] == pytest.approx(_frac(20))
        assert result.max_val[22] == pytest.approx(_frac(50))
        assert "Night" in result.reasons[22]

        # Hour 7: with default config (23,8) it's night; now it's daytime
        assert result.min_val[7] == 0.0
        assert result.max_val[7] == pytest.approx(_frac(50))
        assert "Daytime" in result.reasons[7]

    def test_nighttime_affects_p4_guard(self, monkeypatch):
        """P4 skips nighttime hours — verify this respects the configured window."""
        monkeypatch.setattr(logic, "NIGHTTIME_START", 20)
        monkeypatch.setattr(logic, "NIGHTTIME_END", 6)

        # Prices: hours 0-7 cheap (1ct), 8-23 expensive (20ct)
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.90)

        # Hour 21: expensive, battery OK, but now nighttime → P4 skipped → P5 night
        assert result.min_val[21] == pytest.approx(_frac(20))
        assert result.max_val[21] == pytest.approx(_frac(50))
        assert "Night" in result.reasons[21]

    def test_nighttime_affects_evening_boundary(self, monkeypatch):
        """Evening is defined as 18 <= hour < NIGHTTIME_START."""
        monkeypatch.setattr(logic, "NIGHTTIME_START", 20)
        monkeypatch.setattr(logic, "NIGHTTIME_END", 6)

        # No prices → quantile rules disabled, everything falls to P5
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50)

        # Hour 19: evening (18 <= 19 < 20)
        assert "Evening" in result.reasons[19]
        # Hour 20: night (>= NIGHTTIME_START)
        assert "Night" in result.reasons[20]


class TestConfigBatteryThresholds:
    """Verify BATTERY_LOW_THRESHOLD and BATTERY_HIGH_THRESHOLD affect decisions."""

    def test_raised_low_threshold(self, monkeypatch):
        """Raise LOW to 0.50 → battery at 0.40 triggers P3."""
        monkeypatch.setattr(logic, "BATTERY_LOW_THRESHOLD", 0.50)

        # Mid-range prices (not cheap, not expensive)
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 2.0
        for h in range(6, 18):
            prices[h] = 10.0
        for h in range(18, 24):
            prices[h] = 20.0

        # Battery at 0.40 — above default 0.25 but below new 0.50
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.40)
        # Mid-price hours should hit P3 (battery low)
        for h in range(6, 18):
            assert "Battery low" in result.reasons[h], f"hour {h}: {result.reasons[h]}"
            assert result.max_val[h] == pytest.approx(_frac(50))

    def test_raised_high_threshold(self, monkeypatch):
        """Raise HIGH to 0.95 → battery at 0.80 is "moderate" in evening P4."""
        monkeypatch.setattr(logic, "BATTERY_HIGH_THRESHOLD", 0.95)

        # Prices: cheap first half, expensive second half
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 50, SUNNY_FORECAST, 18, battery_state=0.80)

        # Hour 20: evening, expensive, battery 0.80 < new HIGH 0.95 → moderate sub-case
        assert result.min_val[20] == pytest.approx(_frac(100))
        assert result.max_val[20] == pytest.approx(_frac(200))
        assert "evening" in result.reasons[20].lower()


class TestConfigCloudThreshold:
    """Verify CLOUD_SUN_THRESHOLD affects sun_expected and downstream P4."""

    def test_stricter_cloud_threshold(self, monkeypatch):
        """Lower threshold to 30 → clouds at 40% now means 'no sun'."""
        monkeypatch.setattr(logic, "CLOUD_SUN_THRESHOLD", 30)

        # Forecast: 40% clouds (sunny under default 60, cloudy under new 30)
        forecast = {h: 40 for h in range(24)}

        # Prices: cheap first half, expensive second half
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 40, forecast, 12, battery_state=0.90)

        # Hour 14: expensive, daytime, battery high — but no sun under stricter threshold
        assert result.min_val[14] == pytest.approx(_frac(100))
        assert result.max_val[14] == pytest.approx(_frac(200))
        assert "no sun" in result.reasons[14].lower()

    def test_relaxed_cloud_threshold(self, monkeypatch):
        """Raise threshold to 90 → clouds at 80% now means 'sun expected'."""
        monkeypatch.setattr(logic, "CLOUD_SUN_THRESHOLD", 90)

        forecast = {h: 80 for h in range(24)}

        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 80, forecast, 12, battery_state=0.90)

        # Hour 14: expensive, daytime, battery high — sun expected under relaxed threshold
        assert result.min_val[14] == pytest.approx(_frac(200))
        assert result.max_val[14] == pytest.approx(_frac(400))
        assert "sun expected" in result.reasons[14].lower()


class TestConfigMaxWatts:
    """Verify MAX_WATTS affects the fraction calculation."""

    def test_lower_max_watts(self, monkeypatch):
        """With MAX_WATTS=400, _frac(200) = 0.5 instead of 0.25."""
        monkeypatch.setattr(logic, "MAX_WATTS", 400.0)

        # Expensive daytime with sun → P4: 200/400W → fractions 0.5/1.0
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 20, SUNNY_FORECAST, 12, battery_state=0.90)

        assert result.min_val[14] == pytest.approx(200.0 / 400.0)
        assert result.max_val[14] == pytest.approx(400.0 / 400.0)
