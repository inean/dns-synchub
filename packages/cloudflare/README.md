# dns-synchub-cloudflare

Cloudflare mapper adapter for `dns-synchub`.

## Responsibility

- Consume discovered hosts from pollers.
- Create/update DNS records in Cloudflare.
- Apply retry policy and bounded concurrency.

## Runtime Inputs

- `CF_TOKEN`
- Domain definitions (`DOMAIN__<N>__*`)
- Mapper knobs (`CF_SYNC_SECONDS`, `CF_TIMEOUT_SECONDS`, `CF_MAX_CONCURRENCY`)

## Scope

Cloudflare is the only production DNS provider currently supported.
