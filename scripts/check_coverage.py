#!/usr/bin/env python3
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""Validate coverage thresholds for global and critical runtime modules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

GLOBAL_MIN = 80.0
CRITICAL_MIN = 85.0
TELEMETRY_MIN = 75.0

CRITICAL_FILES: dict[str, float] = {
    'src/dns_synchub/pollers/__init__.py': CRITICAL_MIN,
    'src/dns_synchub/mappers/__init__.py': CRITICAL_MIN,
    'src/dns_synchub/settings/__init__.py': CRITICAL_MIN,
    'src/dns_synchub/__main__.py': CRITICAL_MIN,
    'packages/cli/src/dns_synchub_cli/cli.py': CRITICAL_MIN,
    'packages/cloudflare/src/dns_synchub_cloudflare/cloudflare.py': CRITICAL_MIN,
    'packages/docker/src/dns_synchub_docker/docker.py': CRITICAL_MIN,
    'packages/traefik/src/dns_synchub_traefik/traefik.py': CRITICAL_MIN,
    'src/dns_synchub/meter.py': TELEMETRY_MIN,
    'src/dns_synchub/tracer.py': TELEMETRY_MIN,
}


def _pct(file_data: dict[str, object]) -> float:
    summary = file_data.get('summary', {})
    if not isinstance(summary, dict):
        return 0.0
    value = summary.get('percent_covered', 0.0)
    return float(value)


def main() -> int:
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('coverage.json')
    if not report_path.exists():
        print(f'Coverage report not found: {report_path}', file=sys.stderr)
        return 1

    payload = json.loads(report_path.read_text(encoding='utf-8'))
    files = payload.get('files', {})
    totals = payload.get('totals', {})
    if not isinstance(files, dict) or not isinstance(totals, dict):
        print('Invalid coverage report format.', file=sys.stderr)
        return 1

    failures: list[str] = []
    total = float(totals.get('percent_covered', 0.0))
    if total < GLOBAL_MIN:
        failures.append(f'Global coverage {total:.2f}% < {GLOBAL_MIN:.2f}%')

    for path, threshold in CRITICAL_FILES.items():
        data = files.get(path)
        if not isinstance(data, dict):
            failures.append(f'Missing coverage data for {path}')
            continue
        pct = _pct(data)
        if pct < threshold:
            failures.append(f'{path} coverage {pct:.2f}% < {threshold:.2f}%')

    if failures:
        print('Coverage gate failed:')
        for failure in failures:
            print(f'- {failure}')
        return 1

    print('Coverage gate passed.')
    print(f'- Global: {total:.2f}%')
    for path, threshold in CRITICAL_FILES.items():
        data = files.get(path, {})
        pct = _pct(data if isinstance(data, dict) else {})
        print(f'- {path}: {pct:.2f}% (min {threshold:.2f}%)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
