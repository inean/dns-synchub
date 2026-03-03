# telemetry

Shared telemetry helpers used by `dns-synchub` packages.

## Responsibility

- Provide reusable instrumentation decorators.
- Expose OpenTelemetry-friendly helpers for spans/attributes.

## Notes

- Exporters are environment-driven.
- `none` exporters are the default for local/test runs.
