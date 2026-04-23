"""Tests for solmate_optimizer.logic — exercises every branch of compute_profile."""

import pytest

from solmate_optimizer.logic import (
    HourlyProfile,
    OptimizerConfig,
    _is_night_hour,
    _level,
    _quantile,
    _sun_expected,
    compute_profile,
    parse_level,
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

DEFAULT = OptimizerConfig()

# Prices designed so P25=5, P75=15 (with enough spread).
PRICES_24H: dict[int, float] = {h: 5.0 + (10.0 * h / 23) for h in range(24)}

SUNNY_FORECAST: dict[int, int] = {h: 20 for h in range(24)}  # <60 → sun expected
CLOUDY_FORECAST: dict[int, int] = {h: 80 for h in range(24)}  # >=60 → no sun


def _frac(watts: float, config: OptimizerConfig = DEFAULT) -> float:
    """Shorthand for tests: convert watts to fraction of max capacity."""
    return watts / config.max_watts


def _extreme_prices() -> tuple[dict[int, float], float, float]:
    """Prices with clear low/high separation. Returns (prices, p25, p75)."""
    prices = {h: 2.0 for h in range(12)}  # cheap first half
    prices.update({h: 20.0 for h in range(12, 24)})  # expensive second half
    p25 = _quantile(list(prices.values()), 0.25)
    p75 = _quantile(list(prices.values()), 0.75)
    return prices, p25, p75


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
        assert _is_night_hour(0, DEFAULT) is True

    def test_7_is_night(self):
        assert _is_night_hour(7, DEFAULT) is True

    def test_8_is_not_night(self):
        assert _is_night_hour(8, DEFAULT) is False

    def test_22_is_not_night(self):
        assert _is_night_hour(22, DEFAULT) is False

    def test_23_is_night(self):
        assert _is_night_hour(23, DEFAULT) is True

    def test_12_is_not_night(self):
        assert _is_night_hour(12, DEFAULT) is False


class TestSunExpected:
    def test_sunny_forecast(self):
        assert _sun_expected(SUNNY_FORECAST, DEFAULT) is True

    def test_cloudy_forecast(self):
        assert _sun_expected(CLOUDY_FORECAST, DEFAULT) is False

    def test_empty_forecast(self):
        assert _sun_expected({}, DEFAULT) is False

    def test_partial_forecast_only_morning(self):
        forecast = {h: 30 for h in range(8, 12)}
        assert _sun_expected(forecast, DEFAULT) is True

    def test_threshold_boundary(self):
        forecast = {h: 60 for h in range(8, 18)}
        assert _sun_expected(forecast, DEFAULT) is False


class TestParseLevel:
    def test_valid(self):
        assert parse_level("20,50") == (20.0, 50.0)

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_level("20")

    def test_three_parts(self):
        with pytest.raises(ValueError):
            parse_level("1,2,3")


class TestConfigValidation:
    def test_min_greater_than_max_raises(self):
        with pytest.raises(ValueError, match="min.*<=.*max"):
            OptimizerConfig(level_night=(60.0, 20.0))

    def test_negative_min_raises(self):
        with pytest.raises(ValueError, match=">=.*0"):
            OptimizerConfig(level_low=(-10.0, 50.0))

    def test_max_exceeds_max_watts_raises(self):
        with pytest.raises(ValueError, match="<=.*max_watts"):
            OptimizerConfig(level_high=(200.0, 900.0))

    def test_evening_start_after_nighttime_start_raises(self):
        with pytest.raises(ValueError, match="evening_start.*<=.*nighttime_start"):
            OptimizerConfig(evening_start=23, nighttime_start=22)

    def test_valid_custom_levels(self):
        config = OptimizerConfig(level_night=(10.0, 30.0), level_high=(300.0, 600.0))
        assert config.level_night == (10.0, 30.0)
        assert config.level_high == (300.0, 600.0)


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
        for h in range(12):
            assert result.min_val[h] == 0.0
            assert result.max_val[h] == 0.0
            assert "low" in result.reasons[h].lower() or "P25" in result.reasons[h]

    def test_cheap_price_beats_low_battery(self):
        prices, _, _ = _extreme_prices()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.10)
        for h in range(12):
            assert result.max_val[h] == 0.0


class TestPriority3BatteryLow:
    """Battery < LOW_THRESHOLD → protect battery (level_low) for mid-price hours."""

    def test_low_battery_with_spread_prices(self):
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 2.0
        for h in range(6, 18):
            prices[h] = 10.0
        for h in range(18, 24):
            prices[h] = 20.0

        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.10)
        lo, hi = _level(DEFAULT.level_low, DEFAULT)
        # Hours 0–5: P2 (cheap)
        for h in range(6):
            assert result.max_val[h] == 0.0
        # Hours 6–17: mid-price, battery low → P3 (level_low)
        for h in range(6, 18):
            assert result.min_val[h] == lo
            assert result.max_val[h] == hi
            assert "Battery low" in result.reasons[h]


class TestPriority4HighPrice:
    """Price >= P75, battery OK, not nighttime → inject based on conditions."""

    def _high_price_profile(self) -> dict[int, float]:
        prices: dict[int, float] = {}
        for h in range(12):
            prices[h] = 2.0
        for h in range(12, 23):
            prices[h] = 20.0
        prices[23] = 2.0
        return prices

    def test_high_price_sun_expected(self):
        """P4: high price + battery OK + sun → level_high."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 20, SUNNY_FORECAST, 12, battery_state=0.80)
        lo, hi = _level(DEFAULT.level_high, DEFAULT)
        assert result.min_val[14] == pytest.approx(lo)
        assert result.max_val[14] == pytest.approx(hi)
        assert "sun expected" in result.reasons[14].lower()

    def test_high_price_no_sun(self):
        """P4: high price + battery OK + no sun → level_medium."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 80, CLOUDY_FORECAST, 12, battery_state=0.80)
        lo, hi = _level(DEFAULT.level_medium, DEFAULT)
        assert result.min_val[14] == pytest.approx(lo)
        assert result.max_val[14] == pytest.approx(hi)
        assert "no sun" in result.reasons[14].lower()

    def test_high_price_evening_moderate_battery(self):
        """P4: high price + evening + battery 25–75% → level_medium."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 18, battery_state=0.50)
        lo, hi = _level(DEFAULT.level_medium, DEFAULT)
        assert result.min_val[20] == pytest.approx(lo)
        assert result.max_val[20] == pytest.approx(hi)
        assert "evening" in result.reasons[20].lower()

    def test_high_price_evening_high_battery_sun(self):
        """Evening + high battery → sun_coming check → level_high."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 20, SUNNY_FORECAST, 18, battery_state=0.90)
        lo, hi = _level(DEFAULT.level_high, DEFAULT)
        assert result.min_val[20] == pytest.approx(lo)
        assert result.max_val[20] == pytest.approx(hi)

    def test_high_price_evening_high_battery_no_sun(self):
        """Evening + high battery + no sun → level_medium."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 80, CLOUDY_FORECAST, 18, battery_state=0.90)
        lo, hi = _level(DEFAULT.level_medium, DEFAULT)
        assert result.min_val[20] == pytest.approx(lo)
        assert result.max_val[20] == pytest.approx(hi)

    def test_high_price_nighttime_skips_p4(self):
        """Nighttime + high price → P4 skipped, falls to P5 (level_night)."""
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.90)
        lo, hi = _level(DEFAULT.level_night, DEFAULT)
        assert result.min_val[23] == pytest.approx(lo)
        assert result.max_val[23] == pytest.approx(hi)
        assert "Night" in result.reasons[23] or "baseload" in result.reasons[23]

    def test_high_price_battery_not_ok(self):
        """High price but battery_state=None → battery_ok=False → P4 skipped."""
        prices = self._high_price_profile()
        result = compute_profile(prices, 20, SUNNY_FORECAST, 12, battery_state=None)
        lo, hi = _level(DEFAULT.level_low, DEFAULT)
        assert result.min_val[14] == lo
        assert result.max_val[14] == pytest.approx(hi)


class TestPriority5MiddlePrices:
    """Middle prices (between P25 and P75) → time-of-day based levels."""

    def _mid_price_profile(self) -> dict[int, float]:
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 2.0
        for h in range(6, 18):
            prices[h] = 10.0
        for h in range(18, 24):
            prices[h] = 20.0
        return prices

    def test_night_baseload(self):
        """P5: night hours → level_night."""
        prices = self._mid_price_profile()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        lo, hi = _level(DEFAULT.level_night, DEFAULT)
        for h in [6, 7]:
            assert result.min_val[h] == pytest.approx(lo)
            assert result.max_val[h] == pytest.approx(hi)
            assert "Night" in result.reasons[h] or "baseload" in result.reasons[h]

    def test_daytime_let_pv_charge(self):
        """P5: daytime (8–17) → level_low."""
        prices = self._mid_price_profile()
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        lo, hi = _level(DEFAULT.level_low, DEFAULT)
        for h in range(8, 18):
            assert result.min_val[h] == lo
            assert result.max_val[h] == pytest.approx(hi)
            assert "Daytime" in result.reasons[h] or "PV" in result.reasons[h]

    def test_evening_consumption(self):
        """P5: evening (18–22) → level_evening."""
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 1.0
        for h in range(6, 12):
            prices[h] = 8.0
        for h in range(12, 24):
            prices[h] = 25.0
        for h in [18, 19, 20, 21]:
            prices[h] = 8.0
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        lo, hi = _level(DEFAULT.level_evening, DEFAULT)
        for h in [18, 19, 20, 21]:
            assert result.min_val[h] == pytest.approx(lo), f"hour {h}: {result.reasons[h]}"
            assert result.max_val[h] == pytest.approx(hi), f"hour {h}: {result.reasons[h]}"
            assert "Evening" in result.reasons[h] or "consumption" in result.reasons[h].lower()


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_fewer_than_4_prices_disables_quantile_rules(self):
        prices = {12: 10.0, 13: 10.0}
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        lo, hi = _level(DEFAULT.level_low, DEFAULT)
        assert result.min_val[12] == lo
        assert result.max_val[12] == pytest.approx(hi)

    def test_no_prices_at_all(self):
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        lo, hi = _level(DEFAULT.level_low, DEFAULT)
        assert result.max_val[12] == pytest.approx(hi)
        assert result.reasons[12] == "Daytime, let PV charge"
        nlo, nhi = _level(DEFAULT.level_night, DEFAULT)
        assert result.min_val[23] == pytest.approx(nlo)

    def test_battery_none_skips_priority_3(self):
        prices = {h: 10.0 for h in range(24)}
        prices[0] = 1.0
        prices[23] = 30.0
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=None)
        for h in range(24):
            assert "Battery low" not in result.reasons[h]

    def test_clouds_now_used_when_hour_not_in_forecast(self):
        prices = {h: 10.0 for h in range(24)}
        prices[0] = 1.0
        prices[23] = 30.0
        result = compute_profile(prices, 20, {}, 12, battery_state=0.50)
        assert result is not None
        assert len(result.min_val) == 24

    def test_profile_always_returns_24_elements(self):
        result = compute_profile(PRICES_24H, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        assert len(result.min_val) == 24
        assert len(result.max_val) == 24
        assert len(result.reasons) == 24

    def test_min_never_exceeds_max(self):
        for battery in [0.05, 0.30, 0.50, 0.80, None]:
            for clouds in [10, 80]:
                forecast = SUNNY_FORECAST if clouds < 60 else CLOUDY_FORECAST
                result = compute_profile(PRICES_24H, clouds, forecast, 12, battery_state=battery)
                for h in range(24):
                    assert result.min_val[h] <= result.max_val[h], (
                        f"min > max at hour {h} (battery={battery}, clouds={clouds})"
                    )

    def test_all_values_in_0_1_range(self):
        result = compute_profile(PRICES_24H, 50, SUNNY_FORECAST, 12, battery_state=0.50)
        for h in range(24):
            assert 0.0 <= result.min_val[h] <= 1.0
            assert 0.0 <= result.max_val[h] <= 1.0


# ---------------------------------------------------------------------------
# Tests for configurable parameters via OptimizerConfig
# ---------------------------------------------------------------------------


class TestConfigNighttime:
    def test_custom_nighttime_window(self):
        config = OptimizerConfig(nighttime_start=21, nighttime_end=6)
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50, config=config)
        nlo, nhi = _level(config.level_night, config)
        assert result.min_val[22] == pytest.approx(nlo)
        assert result.max_val[22] == pytest.approx(nhi)
        assert "Night" in result.reasons[22]
        llo, lhi = _level(config.level_low, config)
        assert result.min_val[7] == llo
        assert result.max_val[7] == pytest.approx(lhi)
        assert "Daytime" in result.reasons[7]

    def test_nighttime_affects_p4_guard(self):
        config = OptimizerConfig(nighttime_start=20, nighttime_end=6)
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.90, config=config)
        nlo, nhi = _level(config.level_night, config)
        assert result.min_val[21] == pytest.approx(nlo)
        assert result.max_val[21] == pytest.approx(nhi)
        assert "Night" in result.reasons[21]

    def test_nighttime_affects_evening_boundary(self):
        config = OptimizerConfig(nighttime_start=20, nighttime_end=6)
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50, config=config)
        assert "Evening" in result.reasons[19]
        assert "Night" in result.reasons[20]


class TestConfigEveningStart:
    def test_earlier_evening_start(self):
        """Shift evening to start at 16 → hour 17 becomes evening instead of daytime."""
        config = OptimizerConfig(evening_start=16)
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50, config=config)
        # Hour 17: with default (18) it's daytime; now it's evening
        assert "Evening" in result.reasons[17]
        # Hour 15: still daytime
        assert "Daytime" in result.reasons[15]

    def test_later_evening_start(self):
        """Shift evening to start at 20 → hour 19 becomes daytime instead of evening."""
        config = OptimizerConfig(evening_start=20)
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50, config=config)
        # Hour 19: with default (18) it's evening; now it's daytime
        assert "Daytime" in result.reasons[19]
        # Hour 20: evening
        assert "Evening" in result.reasons[20]


class TestConfigBatteryThresholds:
    def test_raised_low_threshold(self):
        config = OptimizerConfig(battery_low_threshold=0.50)
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 2.0
        for h in range(6, 18):
            prices[h] = 10.0
        for h in range(18, 24):
            prices[h] = 20.0
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.40, config=config)
        lo, hi = _level(config.level_low, config)
        for h in range(6, 18):
            assert "Battery low" in result.reasons[h], f"hour {h}: {result.reasons[h]}"
            assert result.max_val[h] == pytest.approx(hi)

    def test_raised_high_threshold(self):
        config = OptimizerConfig(battery_high_threshold=0.95)
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 50, SUNNY_FORECAST, 18, battery_state=0.80, config=config)
        lo, hi = _level(config.level_medium, config)
        assert result.min_val[20] == pytest.approx(lo)
        assert result.max_val[20] == pytest.approx(hi)
        assert "evening" in result.reasons[20].lower()


class TestConfigCloudThreshold:
    def test_stricter_cloud_threshold(self):
        config = OptimizerConfig(cloud_sun_threshold=30)
        forecast = {h: 40 for h in range(24)}
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 40, forecast, 12, battery_state=0.90, config=config)
        lo, hi = _level(config.level_medium, config)
        assert result.min_val[14] == pytest.approx(lo)
        assert result.max_val[14] == pytest.approx(hi)
        assert "no sun" in result.reasons[14].lower()

    def test_relaxed_cloud_threshold(self):
        config = OptimizerConfig(cloud_sun_threshold=90)
        forecast = {h: 80 for h in range(24)}
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 80, forecast, 12, battery_state=0.90, config=config)
        lo, hi = _level(config.level_high, config)
        assert result.min_val[14] == pytest.approx(lo)
        assert result.max_val[14] == pytest.approx(hi)
        assert "sun expected" in result.reasons[14].lower()


class TestConfigMaxWatts:
    def test_lower_max_watts(self):
        config = OptimizerConfig(max_watts=400.0, level_high=(200.0, 400.0))
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 20, SUNNY_FORECAST, 12, battery_state=0.90, config=config)
        assert result.min_val[14] == pytest.approx(200.0 / 400.0)
        assert result.max_val[14] == pytest.approx(400.0 / 400.0)


class TestConfigLevels:
    """Verify custom injection levels propagate to the profile."""

    def test_custom_night_level(self):
        config = OptimizerConfig(level_night=(40.0, 100.0))
        result = compute_profile({}, 50, SUNNY_FORECAST, 12, battery_state=0.50, config=config)
        lo, hi = _level(config.level_night, config)
        # Hour 0 is nighttime
        assert result.min_val[0] == pytest.approx(lo)
        assert result.max_val[0] == pytest.approx(hi)

    def test_custom_evening_level(self):
        config = OptimizerConfig(level_evening=(80.0, 200.0))
        # Need mid-price evening hours
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 1.0
        for h in range(6, 12):
            prices[h] = 8.0
        for h in range(12, 24):
            prices[h] = 25.0
        for h in [18, 19, 20]:
            prices[h] = 8.0
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.50, config=config)
        lo, hi = _level(config.level_evening, config)
        assert result.min_val[18] == pytest.approx(lo)
        assert result.max_val[18] == pytest.approx(hi)

    def test_custom_high_level(self):
        config = OptimizerConfig(level_high=(300.0, 500.0))
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 20, SUNNY_FORECAST, 12, battery_state=0.90, config=config)
        lo, hi = _level(config.level_high, config)
        assert result.min_val[14] == pytest.approx(lo)
        assert result.max_val[14] == pytest.approx(hi)

    def test_custom_medium_level(self):
        config = OptimizerConfig(level_medium=(150.0, 300.0))
        prices = {h: 1.0 for h in range(8)}
        prices.update({h: 20.0 for h in range(8, 24)})
        result = compute_profile(prices, 80, CLOUDY_FORECAST, 12, battery_state=0.90, config=config)
        lo, hi = _level(config.level_medium, config)
        assert result.min_val[14] == pytest.approx(lo)
        assert result.max_val[14] == pytest.approx(hi)

    def test_custom_low_level(self):
        config = OptimizerConfig(level_low=(10.0, 40.0))
        # Battery low → level_low
        prices: dict[int, float] = {}
        for h in range(6):
            prices[h] = 2.0
        for h in range(6, 18):
            prices[h] = 10.0
        for h in range(18, 24):
            prices[h] = 20.0
        result = compute_profile(prices, 50, SUNNY_FORECAST, 12, battery_state=0.10, config=config)
        lo, hi = _level(config.level_low, config)
        # Hours 6–17: mid-price, battery low → level_low
        for h in range(6, 18):
            assert result.min_val[h] == pytest.approx(lo)
            assert result.max_val[h] == pytest.approx(hi)
