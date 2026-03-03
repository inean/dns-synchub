# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Generator
from logging import Logger
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from requests import exceptions

from dns_synchub.settings import Settings

if TYPE_CHECKING:
    from dns_synchub_traefik import TraefikPoller
else:
    dns_synchub_traefik = pytest.importorskip('dns_synchub_traefik')
    TraefikPoller = dns_synchub_traefik.TraefikPoller


@pytest.fixture
def settings() -> Settings:
    return Settings(cf_token='token', dry_run=True)


@pytest.fixture
def mock_logger() -> Logger:
    return MagicMock(spec=Logger)


@pytest.fixture
def mock_api_no_routers() -> Generator[MagicMock, None, None]:
    with patch('requests.Session.get') as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = []
        yield mock_get


@pytest.fixture
def traefik_poller(mock_logger: MagicMock, settings: Settings) -> TraefikPoller:
    return TraefikPoller(mock_logger, settings=settings)


def test_init(mock_logger: MagicMock, settings: Settings) -> None:
    poller = TraefikPoller(mock_logger, settings=settings)
    assert poller.poll_sec == settings.traefik_poll_seconds
    assert poller.tout_sec == settings.traefik_timeout_seconds
    assert poller.poll_url == f'{settings.traefik_poll_url}/api/http/routers'
    assert 'docker' in poller.excluded_providers


@pytest.mark.asyncio
async def test_no_routers(traefik_poller: TraefikPoller, mock_api_no_routers: MagicMock) -> None:
    data = await traefik_poller.fetch()
    assert data.source == 'traefik'
    assert data.hosts == []


def test_timeout_session_applies_default_timeout() -> None:
    traefik_mod = pytest.importorskip('dns_synchub_traefik.traefik')
    with patch('requests.Session.request') as super_request:
        session = traefik_mod.TimeoutSession(timeout=3)
        session.request('GET', 'http://example.test/api')
        super_request.assert_called_once()
        assert super_request.call_args.kwargs['timeout'] == 3


def test_validate_filters_route_and_hosts(traefik_poller: TraefikPoller) -> None:
    raw_data = [
        {'status': 'disabled', 'name': 'r1', 'rule': 'Host(`a.example.ltd`)'},
        {'status': 'enabled', 'name': 'r2', 'rule': 'Path(`/health`)'},
        {'status': 'enabled', 'name': 'r3', 'rule': 'Host(`sub.example.ltd`)'},
    ]
    data = traefik_poller._validate(raw_data)
    assert data.hosts == ['sub.example.ltd']


@pytest.mark.asyncio
async def test_fetch_retry_error_logs_critical(
    traefik_poller: TraefikPoller, mock_logger: MagicMock
) -> None:
    traefik_poller.config['wait'] = 0
    with patch('requests.Session.get', side_effect=exceptions.RequestException('boom')):
        data = await traefik_poller.fetch()
    assert data.hosts == []
    mock_logger.critical.assert_called()


@pytest.mark.asyncio
async def test_watch_handles_cancelled_error(
    traefik_poller: TraefikPoller, mock_logger: MagicMock
) -> None:
    with (
        patch.object(
            traefik_poller, 'fetch', new=AsyncMock(return_value=traefik_poller._validate([]))
        ),
        patch.object(traefik_poller.events, 'emit', new=AsyncMock(return_value=None)),
        patch('asyncio.sleep', side_effect=asyncio.CancelledError),
    ):
        await traefik_poller._watch()
    mock_logger.info.assert_any_call('Traefik Polling cancelled. Performing cleanup.')


@pytest.mark.asyncio
async def test_route_without_host_is_ignored(traefik_poller: TraefikPoller) -> None:
    with patch('requests.Session.get') as mock_get:
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = [
            {'status': 'enabled', 'name': 'router-without-host', 'rule': 'Path(`/health`)'}
        ]
        data = await traefik_poller.fetch()

    assert data.source == 'traefik'
    assert data.hosts == []
