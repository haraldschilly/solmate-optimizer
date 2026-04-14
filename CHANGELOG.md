## Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CLI options for all remaining env-only variables: `--serial`, `--password`, `--owm-api-key`, `--location`, `--timezone`, `--profile-name`.

## [0.4.0] - 2026-04-13

### Added
- `OptimizerConfig` frozen dataclass replaces module-level globals — all parameters now configurable via CLI options and environment variables (CLI overrides env).
- Named injection levels (`zero`, `night`, `low`, `evening`, `medium`, `high`) with configurable watt pairs via `--level-night`, `--level-low`, `--level-evening`, `--level-medium`, `--level-high` (and corresponding `LEVEL_*` env vars).
- `--evening-start` / `EVENING_START` for configurable evening boundary (default 18).
- `--nighttime` / `NIGHTTIME`, `--battery-low` / `BATTERY_LOW_THRESHOLD`, `--battery-high` / `BATTERY_HIGH_THRESHOLD`, `--cloud-sun-threshold` / `CLOUD_SUN_THRESHOLD`, `--max-watts` / `MAX_WATTS` CLI options.
- Validation of all config values: level min ≤ max, min ≥ 0, max ≤ max_watts, evening_start ≤ nighttime_start.
- Linear interpolation of sparse 3-hour OWM cloud forecast to fill all 24 hours (fixes misleading zigzag in cloud column).
- `p25` and `p75` fields on `HourlyProfile` to avoid duplicate quantile computation.

### Changed
- `logic.py` is now a pure decision engine with no global state — all helpers accept an `OptimizerConfig` parameter.
- Tests use `OptimizerConfig` directly instead of `monkeypatch.setattr` on module globals.
- `plot.py` and `status.py` accept `max_watts` as a parameter instead of importing a global.
- README expanded with injection levels table, full CLI/env configuration reference, and updated example output.

### Removed
- Module-level config globals in `logic.py` (`BATTERY_LOW_THRESHOLD`, `BATTERY_HIGH_THRESHOLD`, `CLOUD_SUN_THRESHOLD`, `MAX_WATTS`, `NIGHTTIME_START`, `NIGHTTIME_END`).

## [0.3.0] - 2026-04-12

### Added
- `BATTERY_HIGH_THRESHOLD` config variable (default 0.75) for battery-aware evening injection.
- Battery-aware peak limiting during evening hours (18:00–22:59): inject 100–200 W when battery is between 25–75 % instead of full power, preserving charge when no solar recharging is possible.

### Fixed
- Plot Y-axis now uses fixed 0 / 200 / 400 W ticks instead of dynamic fractional values; max line renders on top of min line.

### Changed
- README install instructions expanded with pip and uvx options.
- Release process now requires CHANGELOG.md update for every version bump.

## [0.2.0] - 2026-04-11

### Added
- `NIGHTTIME` environment variable to configure the nighttime window (default `23,8`, meaning 23:00 inclusive through 08:00 exclusive).

### Fixed
- Never inject hard (priority 4) during nighttime hours. Previously, a high-price nighttime hour with an OK battery could trigger 200/400W injection, draining the battery overnight when no solar production is possible. Nighttime hours now always fall through to the baseload rule (20/50W) or stricter.

### Changed
- README example output updated to reflect the current plotext line chart format and the nighttime guard behavior.

## [0.1.0] - 2026-04-11

Initial public release.

### Added
- Price-driven 24h injection profile optimizer for EET SolMate.
- Data sources: aWATTar (Austrian day-ahead prices), OpenWeatherMap (clouds + forecast), SolMate SDK (live values + profiles).
- Decision engine with priority rules: negative price, low price (<P25), battery protection, high price (>P75) with sun/no-sun variants, and time-of-day baseload.
- CLI commands: `solmate optimize` (with `--dry-run` and `--no-activate`), `status` (with `--graph` for plotext visualization).
- `TIMEZONE` env var for correct hour matching on UTC servers (default `Europe/Vienna`).
- GitHub Actions release workflow using PyPI trusted publishing (OIDC).
- GCP Cloud Run deployment instructions (`DEPLOYMENT.md`).

[Unreleased]: https://github.com/haraldschilly/solmate-optimizer/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/haraldschilly/solmate-optimizer/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/haraldschilly/solmate-optimizer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/haraldschilly/solmate-optimizer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/haraldschilly/solmate-optimizer/releases/tag/v0.1.0
