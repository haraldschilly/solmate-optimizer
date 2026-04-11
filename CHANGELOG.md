## Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/haraldschilly/solmate-optimizer/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/haraldschilly/solmate-optimizer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/haraldschilly/solmate-optimizer/releases/tag/v0.1.0
