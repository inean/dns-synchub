# dns-synchub-traefik

Traefik poller adapter for `dns-synchub`.

## Responsibility

- Poll Traefik HTTP API for routers.
- Extract `Host(...)` rules and emit discovered hosts.
- Filter out excluded providers and non-matching host patterns.

## Runtime Inputs

- `ENABLE_TRAEFIK_POLL`
- `TRAEFIK_POLL_URL`
- `TRAEFIK_POLL_SECONDS`
- `TRAEFIK_TIMEOUT_SECONDS`
- `TRAEFIK_EXCLUDED_PROVIDERS__<N>`
