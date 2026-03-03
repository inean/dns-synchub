# pyright: reportMissingTypeStubs=false

import sys


def main() -> int:
    try:
        from dns_synchub_cli.cli import cli
    except ImportError:
        print(
            'The CLI package is not installed. Install dns-synchub with the "cli" extra.',
            file=sys.stderr,
        )
        return 1
    return cli()


if __name__ == '__main__':
    raise SystemExit(main())
