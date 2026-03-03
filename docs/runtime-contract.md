# Runtime Contract

## Supported Scope

- DNS provider: Cloudflare only.
- Discovery backends: Docker poller and Traefik poller.
- Extension model: plugin entry points remain available for future providers/pollers.

## Startup Contract

`dns-synchub` resolves settings from env / `.env` and validates:

- Cloudflare token is required unless `dry_run=true`.
- At least one poller must be enabled (`enable_docker_poll` or `enable_traefik_poll`).
- `traefik_poll_url` must be present and valid when Traefik polling is enabled.
- `event_queue_size >= 1`.
- `cf_max_concurrency >= 1`.

## Exit Codes

- `0`: successful execution, or `--show-config` completed.
- `1`: configuration or runtime startup validation error.
- `130`: interrupted/cancelled (`KeyboardInterrupt` or `CancelledError`).

## CLI Contracts

- Script entrypoint: `dns-synchub`.
- Module entrypoint: `python -m dns_synchub`.
- Diagnostics mode: `--show-config` prints resolved runtime config as JSON and exits without starting pollers.

## Poller Event Queue Contract

- Per-subscriber queue is bounded by `event_queue_size`.
- On queue overflow, the oldest pending event is dropped and replaced with the newest event.
- This prioritizes fresh state over stale backlog for long-running daemons.
