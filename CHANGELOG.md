# Changelog

All notable changes to this project will be documented in this file.

## \[Unreleased\]

### Added

- Runtime contract/architecture/operations docs under `docs/`.
- Bounded event queue policy (`EVENT_QUEUE_SIZE`) with overflow handling.
- Cloudflare sync concurrency control (`CF_MAX_CONCURRENCY`).
- CLI diagnostics mode (`--show-config`) with JSON output.
- Strict coverage gate script (`scripts/check_coverage.py`) and CI integration.
- New test suites for CLI, entrypoints, events, logger, meter/tracer, and module entrypoint.

### Changed

- Coverage baseline increased and enforced (`fail_under = 80` plus critical-module checks).
- Container CI smoke checks now explicitly validate runtime CLI entrypoint.
- Package READMEs populated with runtime responsibilities and settings.

### Fixed

- Startup validation now fails fast when no poller is enabled.
- Traefik route and watcher test coverage expanded for reliability paths.
