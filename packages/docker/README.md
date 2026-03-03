# dns-synchub-docker

Docker poller adapter for `dns-synchub`.

## Responsibility

- Discover host rules from container labels.
- Emit host updates to mapper subscribers.
- Retry Docker API event stream reads on transient failures.

## Runtime Inputs

- `ENABLE_DOCKER_POLL`
- `DOCKER_POLL_SECONDS`
- `DOCKER_TIMEOUT_SECONDS`
- `DOCKER_FILTER_LABEL`
- `DOCKER_FILTER_VALUE`
