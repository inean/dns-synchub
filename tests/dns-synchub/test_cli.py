# mypy: disable-error-code=attr-defined
# pyright: reportMissingTypeStubs=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false

import asyncio
import json
from dataclasses import dataclass
from logging import Logger
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from dns_synchub.settings import Settings
from dns_synchub.settings.types import Domains

if TYPE_CHECKING:
    from dns_synchub_cli import cli as dnscli
else:
    dnscli = pytest.importorskip('dns_synchub_cli.cli')


@dataclass
class FakeArgs:
    dry_run: bool = False
    show_config: bool = False
    env_file: str = '/tmp/not-found.env'


def _settings_for_cli() -> Settings:
    return Settings(
        dry_run=True,
        cf_token='token',
        target_domain='target.example.ltd',
        domains=[Domains(zone_id='zone', name='example.ltd')],
    )


def test_parse_args_loads_env_file_when_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        'sys.argv',
        ['dns-synchub', '--env-file', '/tmp/not-found.env', '--dry-run', '--show-config'],
    )
    monkeypatch.setattr(
        dnscli.dotenv,
        'dotenv_values',
        lambda _: {'TARGET_DOMAIN': 'from-file', 'CF_TOKEN': 'from-file'},
    )
    monkeypatch.delenv('TARGET_DOMAIN', raising=False)

    args = dnscli.parse_args()

    assert args.dry_run is True
    assert args.show_config is True


def test_render_config_contains_operational_fields() -> None:
    settings = _settings_for_cli()
    data = dnscli.render_config(settings)
    assert data['cf_max_concurrency'] == settings.cf_max_concurrency
    assert data['event_queue_size'] == settings.event_queue_size
    assert isinstance(data['domains'], list)
    assert data['domains'][0]['name'] == 'example.ltd'


def test_show_config_logs_runtime_options() -> None:
    settings = _settings_for_cli()
    mock_log = MagicMock(spec=Logger)
    dnscli.logger.set_default_logger = MagicMock(return_value=mock_log)

    got = dnscli.show_config(settings)

    assert got is mock_log
    cast(MagicMock, mock_log.debug).assert_called()


@pytest.mark.asyncio
async def test_run_logs_error_when_no_cloudflare_mapper() -> None:
    log = MagicMock(spec=Logger)
    dnscli.Mapper = SimpleNamespace(backends={})  # type: ignore[assignment]

    await dnscli.run(log, settings=_settings_for_cli())

    cast(MagicMock, log.error).assert_called_once_with('No Cloudflare mapper found')


@pytest.mark.asyncio
async def test_run_starts_subscribed_pollers() -> None:
    log = MagicMock(spec=Logger)
    settings = _settings_for_cli()
    settings.enable_traefik_poll = True

    mapper = AsyncMock()
    mapper_factory = MagicMock(return_value=mapper)

    poller = SimpleNamespace(
        events=SimpleNamespace(subscribe=AsyncMock()),
        start=AsyncMock(return_value=None),
    )
    poller_factory = MagicMock(return_value=poller)

    dnscli.Mapper = SimpleNamespace(backends={'cloudflare': mapper_factory})  # type: ignore[assignment]
    dnscli.Poller = SimpleNamespace(  # type: ignore[assignment]
        backends={'docker': poller_factory, 'traefik': poller_factory}
    )

    await dnscli.run(log, settings=settings)

    assert poller_factory.call_count == 2
    assert poller.events.subscribe.await_count == 2
    assert poller.start.await_count == 2


def test_cli_returns_1_on_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dnscli, 'parse_args', lambda: FakeArgs())
    monkeypatch.setattr(
        dnscli.settings, 'Settings', MagicMock(side_effect=ValueError('bad settings'))
    )
    assert dnscli.cli() == 1


def test_cli_show_config_prints_json_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dnscli, 'parse_args', lambda: FakeArgs(show_config=True))
    monkeypatch.setattr(dnscli.settings, 'Settings', MagicMock(return_value=_settings_for_cli()))

    exit_code = dnscli.cli()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload['enable_docker_poll'] is True


def test_cli_returns_130_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_log = MagicMock(spec=Logger)
    monkeypatch.setattr(dnscli, 'parse_args', lambda: FakeArgs())
    monkeypatch.setattr(dnscli.settings, 'Settings', MagicMock(return_value=_settings_for_cli()))
    monkeypatch.setattr(dnscli, 'show_config', MagicMock(return_value=mock_log))
    monkeypatch.setattr(dnscli.asyncio, 'run', MagicMock(side_effect=KeyboardInterrupt()))

    assert dnscli.cli() == 130
    assert cast(MagicMock, mock_log.info).call_count == 2


def test_cli_returns_130_on_cancelled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_log = MagicMock(spec=Logger)
    monkeypatch.setattr(dnscli, 'parse_args', lambda: FakeArgs())
    monkeypatch.setattr(dnscli.settings, 'Settings', MagicMock(return_value=_settings_for_cli()))
    monkeypatch.setattr(dnscli, 'show_config', MagicMock(return_value=mock_log))
    monkeypatch.setattr(dnscli.asyncio, 'run', MagicMock(side_effect=asyncio.CancelledError()))

    assert dnscli.cli() == 130


def test_cli_returns_0_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_log = MagicMock(spec=Logger)
    monkeypatch.setattr(dnscli, 'parse_args', lambda: FakeArgs())
    monkeypatch.setattr(dnscli.settings, 'Settings', MagicMock(return_value=_settings_for_cli()))
    monkeypatch.setattr(dnscli, 'show_config', MagicMock(return_value=mock_log))
    monkeypatch.setattr(dnscli.asyncio, 'run', MagicMock(return_value=None))

    assert dnscli.cli() == 0
