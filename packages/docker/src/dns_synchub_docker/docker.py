# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false

import asyncio
import logging
import re
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from typing import Any, cast, override

import docker
import requests
import tenacity
from docker import DockerClient
from docker.errors import DockerException, NotFound
from docker.models.containers import Container
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dns_synchub.pollers import Poller, PollerData
from dns_synchub.pollers.types import PollerSourceType
from dns_synchub.settings import Settings


class DockerContainer:
    def __init__(self, container: Container, *, logger: logging.Logger):
        self.container = container
        self.logger = logger

    @property
    def id(self) -> str | None:
        return cast(str | None, self.container.attrs.get('Id'))

    @property
    def labels(self) -> Any:
        return self.container.attrs.get('Config', {}).get('Labels', {})

    def __getattr__(self, name: str) -> Any:
        if name in self.container.attrs:
            return self.container.attrs[name]
        return self.labels.get(name)

    @property
    @lru_cache
    def hosts(self) -> list[str]:
        # Try to find traefik filter. If found, tray to parse

        for label, value in self.labels.items():
            if not re.match(r'traefik.*?\.rule', label):
                continue
            self.logger.debug(f"Found traefik label '{label}' from container {self.id}")
            if 'Host' not in value:
                self.logger.debug(f'Malformed rule in container {self.id} - Missing Host')
                continue

            # Extract the domains from the rule
            # Host(`example.com`) => ['example.com']
            hosts = re.findall(r'Host\(`([^`]+)`\)', value)
            self.logger.debug(f"Found service '{self.Name}' with hosts: {hosts}")
            return hosts

        return []


class DockerPoller(Poller[DockerClient]):
    config = {**Poller.config, 'source': 'docker'}  # type: ignore

    def __init__(
        self,
        logger: logging.Logger,
        *,
        settings: Settings,
        client: DockerClient | None = None,
    ):
        # Computed from settings
        self.poll_sec = settings.docker_poll_seconds
        self.tout_sec = settings.docker_timeout_seconds

        # Special, Docker Only filter settings
        self.filter_label = settings.docker_filter_label
        self.filter_value = settings.docker_filter_value

        self._client: DockerClient | None = client

        # Initialize the Poller
        super().__init__(logger, settings=settings, client=client)

    @property
    def client(self) -> DockerClient:
        if self._client is None:
            try:
                # Init Docker client if not provided
                self.logger.debug('Connecting to Docker...')
                self._client = docker.from_env(timeout=self.tout_sec)
            except (DockerException, requests.exceptions.RequestException, OSError) as err:
                self._client = None
                self.logger.error(f'Could not connect to Docker: {err}')
                self.logger.error('Please make sure Docker is running and accessible')
                raise ConnectionError('Could not connect to Docker') from err
            else:
                # Get Docker Host info
                info = cast(dict[str, Any], self._client.info())  # type: ignore[no-untyped-call]
                self.logger.debug(f"Connected to Docker Host at '{info.get('Name')}'")
        return self._client

    def _is_enabled(self, container: DockerContainer) -> bool:
        # If no filter is set, return True
        filter_label = self.filter_label
        if filter_label is None:
            return True
        # Check if any label matches the filter
        for label, value in container.labels.items():
            # Check if label is present
            if filter_label.match(label):
                filter_value = self.filter_value
                if filter_value is None:
                    return True
                # A filter value is also set, check if it matches
                return filter_value.match(value) is not None
        return False

    def _validate(self, raw_data: list[DockerContainer]) -> PollerData[PollerSourceType]:
        hosts: list[str] = []
        for container in raw_data:
            # Check if container is enabled
            if not self._is_enabled(container):
                self.logger.debug(f'Skipping container {container.id}')
                continue
            # Validate domain and queue for sync
            for host in container.hosts:
                hosts.append(host)
        # Return a collection of zones to sync
        return PollerData[PollerSourceType](hosts, self.source)

    @override
    async def _watch(self) -> None:
        until = datetime.now().strftime('%s')

        @retry(
            wait=wait_exponential(multiplier=self.config['wait'], max=self.poll_sec),
            retry=retry_if_exception_type(requests.exceptions.ConnectionError),
            before_sleep=lambda state: self.logger.error(
                f'Retry attempt {state.attempt_number}: {state.outcome.exception() if state.outcome else None}'
            ),
        )
        async def fetch_events(kwargs: dict[str, Any]) -> Iterable[dict[str, Any]] | None:
            kwargs['until'] = datetime.now().strftime('%s')
            return await asyncio.to_thread(self.client.events, **kwargs)

        while True:
            since = until
            self.logger.debug('Fetching routers from Docker API')
            # Ther's no swarm in podman engine, so remove Action filter
            event_filters = {'Type': 'service', 'status': 'start'}
            kwargs = {'since': since, 'filters': event_filters, 'decode': True}
            events: Any | None = None
            try:
                if events := await fetch_events(kwargs):
                    # set by feed events
                    until = str(kwargs['until'])
                for event in events or []:
                    if 'id' not in event:
                        self.logger.warning('Container ID is None. Skipping container.')
                        continue
                    raw_data = await asyncio.to_thread(self.client.containers.get, event['id'])
                    services = [DockerContainer(raw_data, logger=self.logger)]
                    self.events.set_data(self._validate(services))
                else:
                    raise NotFound('No events found')
            except NotFound:
                await self.events.emit()
                await asyncio.sleep(self.poll_sec)
            except tenacity.RetryError as err:
                last_error = err.last_attempt.exception()
                last_error = last_error or RuntimeError(
                    'Retry exhausted without captured exception'
                )
                self.logger.error(f'Could not fetch events: {last_error}')
                raise asyncio.CancelledError from last_error
            except asyncio.CancelledError:
                self.logger.info('Docker polling cancelled. Performing cleanup.')
                raise
            finally:
                if events is not None:
                    await asyncio.to_thread(events.close)

    @override
    async def fetch(self) -> PollerData[PollerSourceType]:
        filters = {'status': 'running'}
        stop = stop_after_attempt(self.config['stop'])
        wait = wait_exponential(multiplier=self.config['wait'], max=self.tout_sec)
        result: list[DockerContainer] = []
        try:
            async for attempt_ctx in AsyncRetrying(stop=stop, wait=wait):
                with attempt_ctx:
                    try:
                        containers = self.client.containers
                        raw_data = await asyncio.to_thread(containers.list, filters=filters)
                        result = [DockerContainer(c, logger=self.logger) for c in raw_data]
                    except (DockerException, requests.exceptions.RequestException, OSError) as err:
                        att = attempt_ctx.retry_state.attempt_number
                        self.logger.debug(f'Docker.fetch attempt {att} failed: {err}')
                        raise
        except RetryError as err:
            last_error = err.last_attempt.exception()
            self.logger.critical(f'Could not fetch containers: {last_error}')
            raise ConnectionError('Could not fetch containers') from last_error
        # Return a collection of routes
        return self._validate(result)
