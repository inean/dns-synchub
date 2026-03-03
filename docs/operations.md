# Operations Runbook

## Preconditions

- Python `>=3.13`.
- Cloudflare token available (`CF_TOKEN`) unless `DRY_RUN=true`.
- At least one discovery mode enabled.

## Health Checks

1. Validate settings only:
   - `dns-synchub --show-config`
1. Verify module entrypoint:
   - `python -m dns_synchub --version`
1. Verify package script entrypoint:
   - `dns-synchub --version`

## Common Failure Modes

- `Unable to load settings: Missing Cloudflare API token...`
  - Provide `CF_TOKEN` or run with `DRY_RUN=true`.
- `At least one poller must be enabled`
  - Enable Docker and/or Traefik poller.
- `Invalid Traefik polling URL`
  - Set `TRAEFIK_POLL_URL` with scheme and host.

## Runtime Notes

- Event delivery is bounded per subscriber queue. If consumer is slow, oldest events are dropped.
- Cloudflare sync concurrency is controlled by `CF_MAX_CONCURRENCY`.
- Shutdown is cooperative; `Ctrl+C` should return exit code `130`.

## Release Checklist (Operational)

1. `uv run pre-commit run --all-files`
1. `uv run pytest -q`
1. `uv run mypy`
1. `uv run pyright`
1. `uv run coverage run -m pytest && uv run coverage report`
1. Container smoke:
   - `docker build -t dns-synchub:test .`
   - `docker run --rm dns-synchub:test --version`
