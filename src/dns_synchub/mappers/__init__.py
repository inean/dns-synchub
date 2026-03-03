from abc import ABC, abstractmethod
from functools import lru_cache
from importlib import metadata
from logging import Logger
from typing import (
    Any,
    Generic,
    Protocol,
    TypedDict,
    TypeVar,
    runtime_checkable,
)

from dns_synchub.events.types import (
    EventSubscriber,
    EventSubscriberType as EventSubscriberType,
)
from dns_synchub.pollers.types import (
    PollerSourceType as PollerSourceType,
)
from dns_synchub.settings import Settings
from dns_synchub.settings.types import (
    Domains,
)
from dns_synchub.tracer import telemetry_tracer
from dns_synchub.utils._classproperty import classproperty

T = TypeVar('T')  # Client backemd
E = TypeVar('E')  # Event type accepted
R = TypeVar('R')  # Result type


@runtime_checkable
class MapperProtocol(EventSubscriber[E], Protocol[E, R]):
    @abstractmethod
    async def sync(self, data: E) -> list[R] | None: ...


class MapperConfig(TypedDict):
    wait: int
    """Factor to multiply the backoff time by"""
    stop: int
    """Max number of retries to attempt before exponential backoff fails"""
    delay: float
    """Delay in seconds before syncing mappings"""


class BaseMapper(ABC, MapperProtocol[E, R], Generic[E, R]):
    config: MapperConfig = {
        'stop': 3,
        'wait': 4,
        'delay': 0,
    }

    def __init__(self, logger: Logger):
        self.logger = logger
        self.tracer = telemetry_tracer().get_tracer('otel.instrumentation.mappers')


class Mapper(BaseMapper[E, Domains], Generic[E, T]):
    def __init__(self, logger: Logger, *, settings: Settings, client: T | None = None):
        # init client
        self._client: T | None = client

        # Domain defaults
        self.dry_run = settings.dry_run
        self.rc_type = settings.rc_type
        self.refresh_entries = settings.refresh_entries

        # Computed from settings
        self.domains = settings.domains
        self.included_hosts = settings.included_hosts
        self.excluded_hosts = settings.excluded_hosts

        super().__init__(logger)

    @property
    def client(self) -> T:
        if self._client is None:
            raise RuntimeError('Client is not initialized')
        return self._client

    @classproperty
    @lru_cache(maxsize=None)
    def backends(cls) -> dict[str, Any]:
        backends: dict[str, Any] = {}
        for entry_point in metadata.entry_points(group='dns_synchub.mappers'):
            backends[entry_point.name] = entry_point.load()
        return backends
