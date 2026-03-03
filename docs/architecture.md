# Architecture

## Components

- `dns_synchub_cli`: CLI bootstrap, settings resolution, runtime orchestration.
- `dns_synchub.settings`: validated runtime configuration.
- `dns_synchub.pollers`: discovery interface and poller lifecycle.
- `dns_synchub.mappers`: DNS provider interface and mapping lifecycle.
- `dns_synchub.events`: async event emitter with backoff and bounded queues.
- `dns_synchub_*` packages:
  - `dns_synchub_docker`: Docker discovery adapter.
  - `dns_synchub_traefik`: Traefik discovery adapter.
  - `dns_synchub_cloudflare`: Cloudflare mapping adapter.
  - `telemetry`: shared decorator/instrumentation helpers.

## Data Flow

1. CLI loads/validates `Settings`.
1. Pollers (`docker`, `traefik`) discover hosts and emit `PollerData`.
1. EventEmitter forwards data to subscribed mapper callback.
1. Cloudflare mapper resolves desired DNS records and performs create/update operations.
1. Telemetry and logging capture runtime behavior.

## Extension Points

- Pollers: `dns_synchub.pollers` entry point group.
- Mappers: `dns_synchub.mappers` entry point group.

Current production scope keeps Cloudflare as the only mapper implementation, while extension points remain stable for future adapters.
