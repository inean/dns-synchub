#!/usr/bin/env python3

from __future__ import print_function

import asyncio
import logging
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from asyncio import Queue, iscoroutinefunction
from collections.abc import AsyncGenerator, Callable
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from logging import Logger
from typing import Any, TypedDict
from urllib.parse import urlparse

import docker
import docker.errors
import requests

# Inject custom methods into EventSettingsSource tu get support for:
# - _FIELD  like env vars
# -  List based submodules so FOO__0__KEY=VALUE will be converted to FOO=[{'KEY': 'VALUE'}]
#
from _settings import _EnvSettingsSource
from CloudFlare import CloudFlare
from CloudFlare import exceptions as CloudFlareExceptions
from pydantic import BaseModel, ValidationError, model_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
from typing_extensions import Self, deprecated, override

EnvSettingsSource.get_field_value = _EnvSettingsSource.get_field_value
EnvSettingsSource.explode_env_vars = _EnvSettingsSource.explode_env_vars


class AsyncRepeatedTimer:
    def __init__(
        self,
        interval: int,
        function: callable,
        *,
        args: tuple | None = None,
        kwargs: dict | None = None,
    ):
        self.interval = interval
        self.function = function
        self.args = args or []
        self.kwargs = kwargs or {}
        self.task = None

    async def _run(self):
        while True:
            await asyncio.sleep(self.interval)
            self.function(*self.args, **self.kwargs)

    def start(self):
        self.task = asyncio.create_task(self._run())
        return self.task

    def cancel(self):
        self.task and self.task.cancel()


class DomainsModel(BaseModel):
    name: str
    zone_id: str
    proxied: bool = True
    ttl: int | None = None
    target_domain: str | None = None
    comment: str | None = None
    excluded_sub_domains: list[str] = []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        validate_default=False,
        extra="ignore",
        secrets_dir="/var/run",
        env_file=(".env", ".env.prod"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
    )

    # Settings
    dry_run: bool = False
    log_file: str = "/logs/tcc.log"
    log_level: str = "INFO"
    log_type: str = "BOTH"

    # Docker Settings
    enable_docker_poll: bool = True
    docker_poll_seconds: int = 5

    # Traefik Settings
    enable_traefik_poll: bool = False
    traefik_filter_value: str | None = None
    traefik_filter_label: str = "traefik.constraint"
    refresh_entries: bool = False
    traefik_poll_seconds: int = 5
    traefik_poll_url: str | None = None
    traefik_included_hosts: list[re.Pattern] = []
    traefik_excluded_hosts: list[re.Pattern] = []

    # Cloudflare Settings
    cf_token: str
    target_domain: str

    cf_email: str | None = None  # If not set, we are using scoped API
    default_ttl: int = 1
    rc_type: str = "CNAME"
    domains: list[DomainsModel] = []

    @model_validator(mode="after")
    def update_domains(self) -> Self:
        for dom in self.domains:
            dom.ttl = dom.ttl or self.default_ttl
            dom.target_domain = dom.target_domain or self.target_domain
        return self

    @model_validator(mode="after")
    def update_traefik_domains(self) -> Self:
        if len(self.traefik_included_hosts) == 0:
            self.traefik_included_hosts.append(re.compile(".*"))
        return self

    @model_validator(mode="after")
    def sanity_options(self) -> Self:
        if self.enable_traefik_poll and not self.traefik_poll_url:
            raise ValueError("Traefik Polling is enabled but no URL is set")
        return self


# set up logging
def initialize_logger(settings):
    # Extract attributes from settings and convert to uppercase
    log_level = settings.log_level.upper()
    log_type = settings.log_type.upper()
    log_file = settings.log_file

    # Set up logging
    logger = logging.getLogger(__name__)

    if log_level == "DEBUG":
        logger.setLevel(logging.DEBUG)
        fmt = "%(asctime)s %(levelname)s %(lineno)d | %(message)s"

    if log_level == "VERBOSE":
        logger.setLevel(logging.DEBUG)
        fmt = "%(asctime)s %(levelname)s | %(message)s"

    if log_level in ("NOTICE", "INFO"):
        logger.setLevel(logging.INFO)
        fmt = "%(asctime)s %(levelname)s | %(message)s"

    if log_type in ("CONSOLE", "BOTH"):
        ch = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(fmt, "%Y-%m-%dT%H:%M:%S%z")
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    if log_type in ("FILE", "BOTH"):
        try:
            fh = logging.FileHandler(log_file)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError as e:
            logger.error(f"Could not open log file '{e.filename}': {e.strerror}")

    return logger


def is_domain_excluded(logger, name, dom: DomainsModel):
    for sub_dom in dom.excluded_sub_domains:
        if f"{sub_dom}.{dom.name}" in name:
            logger.info(f"Ignoring {name} because it falls until excluded sub domain: {sub_dom}")
            return True
    return False


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(cls, *args, **kwargs)
        return cls._instances[cls]


class CloudFlareConfig(TypedDict, total=False):
    max_retries: int
    """Max number of retries to attempt before exponential backoff fails"""


class CloudFlareException(Exception):
    pass


class CloudFlareZones(metaclass=Singleton):
    config: CloudFlareConfig = {
        "max_retries": 5,
    }

    def __init__(self, settings: Settings, logger: Logger, *, client: CloudFlare | None = None):
        if client is None:
            assert settings.cf_token is not None
            client = CloudFlare(
                email=settings.cf_email,
                token=settings.cf_token,
                debug=settings.log_level.upper() == "VERBOSE",
            )
            logger.debugf(f"API Mode: {'Scoped' if not settings.cf_email else 'Global'}")

        # Set up the client and logger
        self.client = client
        self.logger = logger

        # Extract required settings
        self.dry_run = settings.dry_run
        self.rc_type = settings.rc_type
        self.refresh_entries = settings.refresh_entries

    async def get_records(self, zone_id: str, name: str):
        for retry in range(self.config["max_retries"]):
            try:
                return self.client.zones.dns_records.get(zone_id, params={"name": name})
            except CloudFlareExceptions.CloudFlareAPIError as err:
                if "Rate limited" not in str(err):
                    raise err
                # Exponential backoff
                sleep_time = 2 ** (retry + 1)
                self.logger.warning(f"Max Rate limit reached. Retry in {sleep_time} seconds...")
                asyncio.sleep(sleep_time)
        raise CloudFlareException("Max retries exceeded")

    def post_record(self, zone_id, data):
        if self.dry_run:
            self.logger.info(f"DRY-RUN: POST to Cloudflare {zone_id}:, {data}")
        else:
            self.client.zones.dns_records.post(zone_id, data=data)
            self.logger.info(f"Created new record in zone {zone_id} with data {data}")

    def put_record(self, zone_id, record_id, data):
        if self.dry_run:
            self.logger.info(f"DRY-RUN: PUT to Cloudflare {zone_id}, {record_id}:, {data}")
        else:
            self.client.zones.dns_records.put(zone_id, record_id, data=data)
            self.logger.info(f"Updated record {record_id} in zone {zone_id} with data {data}")

    # Start Program to update the Cloudflare
    async def update_zones(self, name, domain_infos: list[DomainsModel]):
        ok = True
        for domain_info in domain_infos:
            # Don't update the domain if it's the same as the target domain, which sould be used on tunnel
            if name == domain_info.target_domain:
                continue
            # Skip if it's not a subdomain of the domain we're looking for
            if name.find(domain_info.name) < 0:
                continue
            # Skip if the domain is exclude list
            if is_domain_excluded(self.logger, name, domain_info):
                continue
            # Fetch the records for the domain, if any
            if (records := await self.get_records(domain_info.zone_id, name)) is None:
                ok = False
                continue
            # Prepare data for the new record
            data = {
                "type": self.rc_type,
                "name": name,
                "content": domain_info.target_domain,
                "ttl": int(domain_info.ttl),
                "proxied": domain_info.proxied,
                "comment": domain_info.comment,
            }
            try:
                # Update the record if it already exists
                if self.refresh_entries and len(records) > 0:
                    for record in records:
                        self.put_record(domain_info.zone_id, record["id"], data)
                # Create a new record if it doesn't exist yet
                else:
                    self.post_record(domain_info.zone_id, data)
            except CloudFlare.exceptions.CloudFlareAPIError as ex:
                self.logger.error("** %s - %d %s" % (name, ex, ex))
                ok = False
        return ok

    # Start Program to update the Cloudflare
    @deprecated("Use update_zones instead")
    @staticmethod
    def point_domain(settings, name, domain_infos: list[DomainsModel], logger):
        client = CloudFlareZones(settings, logger)
        return asyncio.run(client.update_zones(name, domain_infos))


class PollerSource(Enum):
    MANUAL = "manual"
    DOCKER = "docker"
    TRAEFIK = "traefik"


class Poller(ABC):
    def __init__(self, logger: logging.Logger, *, client: Any):
        """
        Initializes the Poller with a logger and a client.

        Args:
            logger (logging.Logger): The logger instance for logging.
            client (Any): The client instance for making requests.
        """
        self.logger = logger
        self.client = client

        # Event notifers
        self.running = False
        self._subscribers: dict[Callable, asyncio.Queue] = {}

    # Poller methods
    @abstractmethod
    async def fetch(self):
        """
        Abstract method to fetch data.
        Must be implemented by subclasses.
        """
        pass

    @abstractmethod
    async def run(self, *, timeout: float | None = None):
        """
        Abstract method to watch for changes. This method must emit signals
        whenever new data is available.

        Args:
            timeout (float | None): The timeout duration in seconds. If None,
                                    the method will wait indefinitely.

        Must be implemented by subclasses.
        """
        pass

    # Event methods
    async def subscribe(self, callback: Callable):
        """
        Subscribes to events from this Poller.

        Args:
            callback (Callable): The callback function to be called when an event is emitted.
        """
        # Register subscriber
        self._subscribers.setdefault(callback, asyncio.Queue())
        # Fetch data and store locally if requred
        self._subscribers or self.set_data(await self.fetch())

    def unsubscribe(self, callback: Callable):
        """
        Unsubscribes from events.

        Args:
            callback (Callable): The callback function to be removed from subscribers.
        """
        self._subscribers.pop(callback, None)

    async def emit(self):
        """
        Triggers an event and notifies all subscribers.
        Calls each subscriber's callback with the data.
        """
        for callback, queue in self._subscribers.items():
            while not queue.empty():
                data = await queue.get()
                if iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)

    # Data related methods
    def set_data(self, data):
        """
        Sets the data for all subscribers.

        Args:
            data: The data to be set for subscribers.
        """
        for queue in self._subscribers.values():
            queue.put_nowait(data)

    def has_data(self, callback: Callable):
        """
        Checks if there is data available for a specific subscriber.

        Args:
            callback (Callable): The callback function to check for data.

        Returns:
            bool: True if there is data available, False otherwise.
        """
        return callback in self._subscribers and not self._subscribers[callback].empty()

    async def get_data(self, callback: Callable):
        """
        Gets the data for a specific subscriber.

        Args:
            callback (Callable): The callback function to get data for.

        Returns:
            The data for the specified callback.
        """
        if callback in self._subscribers:
            return await self._subscribers[callback].get()


class DataPoller(Poller):
    def __init__(self, logger, *, settings: Settings, client: Any):
        super(DataPoller, self).__init__(logger, client=client)

        # Computed from settings
        self.included_hosts = settings.traefik_included_hosts
        self.excluded_hosts = settings.traefik_excluded_hosts


class TraefikPoller(DataPoller):
    def __init__(self, logger, *, settings: Settings, client: requests.Session | None = None):
        super(TraefikPoller, self).__init__(
            logger,
            settings=settings,
            client=client or requests.Session(),
        )
        # Computed from settings
        self.poll_sec = settings.traefik_poll_seconds
        self.poll_url = f"{settings.traefik_poll_url}/api/http/routers"

    def _is_valid_route(self, route):
        # Computed from settings
        required_keys = ["status", "name", "rule"]
        if any(key not in route for key in required_keys):
            self.logger.debug(f"Traefik Router Name: {route} - Missing Key")
            return False
        if route["status"] != "enabled":
            self.logger.debug(f"Traefik Router Name: {route['name']} - Not Enabled")
            return False
        if "Host" not in route["rule"]:
            self.logger.debug(f"Traefik Router Name: {route['name']} - Missing Host")
        # Route is valid and enabled
        return True

    def _is_valid_host(self, host):
        if not any(pattern.match(host) for pattern in self.included_hosts):
            self.logger.debug(f"Traefik Router Host: {host} - Not Match with Include Hosts")
            return False
        if any(pattern.match(host) for pattern in self.excluded_hosts):
            self.logger.debug(f"Traefik Router Host: {host} - Match with Exclude Hosts")
            return False
        # Host is intended to be synced
        return True

    def _validate(self, raw_data: list[dict]) -> tuple[list[str], PollerSource]:
        data = []
        for route in raw_data:
            # Check if route is whell formed
            if not self._is_valid_route(route):
                continue
            # Extract the domains from the rule
            hosts = re.findall(r"Host\(`([^`]+)`\)", route["rule"])
            self.logger.debug(f"Traefik Router Name: {route['name']} domains: {hosts}")
            # Validate domain and queue for sync
            for host in (host for host in hosts if self._is_valid_host(host)):
                self.logger.info(f"Found Traefik Router: {route['name']} with Hostname {host}")
                data.append(host)
        # Return a collection of zones to sync
        return data, PollerSource.TRAEFIK

    @override
    def fetch(self) -> tuple[list[str, PollerSource]]:
        try:
            response = self.client.get(self.poll_url, self.poll_sec)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to fetch routers from Traefik API: {e}")
            response = None
            # Return a collection of routes
        return self._validate([] if response is None else response.json())

    @override
    async def run(self, timeout: float | None = None):
        async def _watch() -> AsyncGenerator[list[dict], None]:
            try:
                while True:
                    self.logger.debug("Fetching routers from Traefik API")
                    self.set_data(await self.fetch())
                    self.emit()
                    await asyncio.sleep(self.poll_sec)
            except asyncio.CancelledError:
                self.logger.info("Polling has been cancelled. Performing cleanup.")
                pass

        self.logger.info(f"Starting Traefik Poller: Watching Traefik every {self.poll_sec}")
        # self.fetch is called for the firstime, whehever a a client subscribe to
        # this poller, so ther0's no need to initialy  fetch data
        if timeout:
            until = datetime.now() + timedelta(seconds=timeout)
            self.logger.debug(f"Stop prograamed at {until}")
            try:
                await asyncio.wait_for(_watch, timeout)
            except asyncio.TimeoutError:
                self.logger.info(f"Traefik Polling timeout '{until}'reached")
        else:
            # Run indefinitely.
            await _watch()


@deprecated("Use TraefikPoller")
class TraefikLegacyPoller(TraefikPoller):
    @deprecated("Use run instead")
    def check_traefik(self):
        def is_matching(host, patterns):
            return any(pattern.match(host) for pattern in patterns)

        mappings = {}
        self.logger.debug("Called check_traefik poller")
        response = requests.get(self.poll_url)

        if response is not None and response.ok:
            for router in response.json():
                if any(key not in router for key in ["status", "name", "rule"]):
                    self.logger.debug(f"Traefik Router Name: {router} - Missing Key")
                    continue
                if router["status"] != "enabled" or "Host" not in router["rule"]:
                    self.logger.debug(
                        f"Traefik Router Name: {router['name']} - Not Enabled or Missing Host"
                    )
                    continue

                # Extract the domains from the rule
                name, value = router["name"], router["rule"]
                self.logger.debug(f"Traefik Router Name: {name} rule value: {value}")

                # Extract the domains from the rule
                extracted_domains = re.findall(r"Host\(`([^`]+)`\)", value)
                self.logger.debug(f"Traefik Router Name: {name} domains: {extracted_domains}")

                for v in extracted_domains:
                    if not is_matching(v, self.included_hosts):
                        self.logger.debug(
                            f"Traefik Router Name: {name} with Host {v}: Not Match Include"
                        )
                        continue
                    if is_matching(v, self.excluded_hosts):
                        self.logger.debug(
                            f"Traefik Router Name: {name} with Host {v} - Match exclude"
                        )
                        continue
                    # Matched
                    self.logger.info(f"Found Traefik Router Name: {name} with Hostname {v}")
                    mappings[v] = 2

        return mappings

    @deprecated("Use run instead")
    def check_traefik_and_sync_mappings(self, domain_infos):
        """
        Checks Traefik for mappings and syncs them with the domain information.

        Args:
            included_hosts (list): List of hosts to include.
            excluded_hosts (list): List of hosts to exclude.
            domain_infos (dict): Domain information for synchronization.
        """
        # Extract mappings from Traefik
        traefik_mappings = self.check_traefik()
        # Sync the extracted mappings with the domain information
        sync_mappings(self.settings, traefik_mappings, domain_infos)


@deprecated("Use TraefikPoller instead")
def check_traefik(
    settings, included_hosts: list[re.Pattern], excluded_hosts: list[re.Pattern], logger
):
    settings.traefik_included_hosts = included_hosts
    settings.traefik_excluded_hosts = excluded_hosts
    poller = TraefikLegacyPoller(logger, settings=settings)
    return poller.check_traefik()


@deprecated("Use TraefikPoller instead")
def check_traefik_and_sync_mappings(settings, included_hosts, excluded_hosts, domain_infos, logger):
    settings.traefik_included_hosts = included_hosts
    settings.traefik_excluded_hosts = excluded_hosts
    poller = TraefikLegacyPoller(logger, settings=settings)
    poller.check_traefik_and_sync_mappings(included_hosts, excluded_hosts, domain_infos)


class ZoneUpdateJob(TypedDict, total=False):
    timestamp: int
    """Timestamp of the job. Use time.monotonic_ns()"""

    source: PollerSource
    """Poller that provide the job"""

    entries: list[str]
    """List of entries to update"""


class SyncManager(metaclass=Singleton):
    def __init__(self, queue: Queue | None = None):
        self.queue = queue or Queue(max_size=len(PollerSource))
        self.entries = {}

    async def put(self, entries: list[str], source: PollerSource):
        await self.queue.put(
            ZoneUpdateJob(
                timestamp=time.monotonic_ns(),
                source=source,
                entries=entries,
            )
        )


class DataManager:
    def __init__(self, *logger: logging.Logger):
        self.pollers: list[Poller] = []
        self.tasks = []
        self.logger = logger

    def __call__(self, data):
        raise NotImplementedError

    def _combine_data(self, all_data):
        """Combine data from multiple pollers. Customize as needed."""
        combined = {}
        for data in all_data:
            if data:
                combined.update(data)  # Example combination logic
        return combined

    def add_poller(self, poller: Poller):
        """Add a DataPoller to the manager."""
        self.pollers.append(poller)

    async def start(self, timeout: float | None = None):
        """Start all pollers by fetching initial data and subscribing to events."""
        assert len(self.tasks) == 0
        # Loop pollers
        for poller in self.pollers:
            # Register itelf to be called when new data is available
            poller.subscribe(self)
            # Ask poller to start monitoring data
            self.tasks.append(poller.run(timeout=timeout))
        try:
            # wait until timeout is reached or tasks are anceled
            await asyncio.gather(*self.tasks)
        except asyncio.CancelledError:
            # Gracefully stop monitoring
            await self.stop()
        except KeyboardInterrupt:
            # Handle keyboard interruption
            self.logger.info("Keyboard interruption detected. Stopping tasks...")
            await self.stop()

    async def stop(self):
        """Unsubscribe all pollers from their event systems."""
        # This could be extended to stop any running background tasks if needed
        tasks = [task.cancel() for task in self.tasks]
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    async def aggregate_data(self):
        """Aggregate and return the latest data from all pollers."""
        tasks = [poller.get_data() for poller in self.pollers]
        all_data = await asyncio.gather(*tasks)
        return self._combine_data(all_data)


synced_mappings = {}


@deprecated("Use SyncManager instead")
def add_to_mappings(current_mappings, mappings):
    """
    Adds new mappings to the current mappings if they meet the criteria.

    Args:
        current_mappings (dict): The current mappings.
        mappings (dict): The new mappings to add.
    """
    for k, v in mappings.items():
        current_mapping = current_mappings.get(k)
        if current_mapping is None or current_mapping > v:
            current_mappings[k] = v


@deprecated("Use SyncManager instead")
def sync_mappings(settings, mappings, domain_infos, logger):
    """
    Synchronizes the mappings with the domain information.

    Args:
        mappings (dict): The mappings to synchronize.
        domain_infos (dict): Domain information for synchronization.
    """
    for k, v in mappings.items():
        current_mapping = synced_mappings.get(k)
        if current_mapping is None or current_mapping > v:
            if CloudFlareZones.point_domain(settings, k, domain_infos, logger):
                synced_mappings[k] = v


def get_initial_mappings(client, settings: Settings, included_hosts, excluded_hosts, logger):
    """
    Initializes the mappings by discovering Docker containers and Traefik services.

    Args:
        included_hosts (list): List of hosts to include.
        excluded_hosts (list): List of hosts to exclude.

    Returns:
        dict: The initial mappings.
    """
    logger.debug("Starting Initialization Routines")

    mappings = {}
    if settings.enable_docker_poll:
        for c in client.containers.list():
            logger.debug("Container List Discovery Loop")
            add_to_mappings(mappings, check_container_t2(c, settings))

    if settings.traefik_poll_url:
        logger.debug("Traefik List Discovery Loop")
        # Extract mappings from Traefik
        traefik_mappings = check_traefik(settings, included_hosts, excluded_hosts, logger)
        # Add the extracted mappings to the current mappings
        add_to_mappings(mappings, traefik_mappings)

    return mappings


def uri_valid(x):
    try:
        result = urlparse(x)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def report_current_status_and_settings(logger: logging.Logger, settings: Settings):
    if settings.dry_run:
        logger.warning(f"Dry Run: {settings.dry_run}")
    logger.debug(f"Docker Polling: {settings.enable_docker_poll}")
    logger.debug(f"Refresh Entries: {settings.refresh_entries}")
    logger.debug(f"Default TTL: {settings.default_ttl}")

    if settings.enable_traefik_poll:
        if uri_valid(settings.traefik_poll_url):
            logger.debug("Traefik Poll Url: %s", settings.traefik_poll_url)
            logger.debug("Traefik Poll Seconds: %s", settings.traefik_poll_seconds)
        else:
            settings.enable_traefik_poll = False
            logger.error(
                "Traefik Polling Mode disabled because traefik url is invalid: %s",
                settings.traefik_poll_url,
            )

    logger.debug("Traefik Polling Mode: %s", False)

    for dom in settings.domains:
        logger.debug("Domain Configuration: %s", dom)


class DockerContainer:
    def __init__(self, container: dict, *, logger: logging.Logger):
        self.container = container
        self.logger = logger

    @property
    def id(self) -> str | None:
        return self.container.attrs.get("Id")

    @property
    def labels(self) -> dict[str, str]:
        return self.container.attrs.get("Config", {}).get("Labels", {})

    def __getattr__(self, name: str) -> str | None:
        return self.labels.get(name)

    @property
    @lru_cache
    def hosts(self) -> list[str]:
        # Try to find traefik filter. If found, tray to parse

        for label, value in self.labels.items():
            if re.match(r"traefik.*?\.rule", label):
                self.logger.debug(f"Skipping label {label} from container {self.id}")
                continue
            if "Host" not in value:
                self.logger.debug(f"Skipping container {self.id} - Missing Host")
                continue

            # Extract the domains from the rule
            # hosts = re.findall(r"\`([a-zA-Z0-9\.\-]+)\`", value)
            hosts = re.findall(r"Host\(`([^`]+)`\)", value)
            self.logger.debug(f"Docker Route Name: {self.id} domains: {hosts}")
            return [hosts] if isinstance(hosts, str) else hosts

        return []


class DockerPoller(Poller):
    def __init__(self, settings: Settings, logger, *, client: docker.DockerClient | None = None):
        # Init Docker client
        try:
            client = client or docker.from_env(timeout=settings.docker_poll_seconds)
        except docker.errors.DockerException as e:
            logger.error(f"Could not connect to Docker: {e}")
            logger.error(f"Known DOCKER_HOST env is '{os.getenv('DOCKER_HOST') or ''}'")
            sys.exit(1)

        logger.debug("Connected to Docker")
        super(DockerPoller, self).__init__(settings, logger, client)

        # Computed from settings
        self.poll_sec = settings.docker_poll_seconds
        self.filter_label = re.compile(settings.traefik_filter_label)
        self.filter_value = re.compile(settings.traefik_filter_value)

    def is_enabled(self, container: DockerContainer):
        for label, value in container.labels.items():
            if self.filter_label.match(label) and self.filter_value.match(value):
                return True
        return False

    @override
    async def fetch(self) -> list[DockerContainer]:
        return [
            DockerContainer(container, logger=self.logger)
            for container in self.client.containers.list()
        ]

    @override
    async def poll(self) -> AsyncGenerator[list[DockerContainer], None]:
        while True:
            yield self.fetch()
            await asyncio.sleep(self.poll_sec)

    @override
    async def run(self):
        self.logger.info("Starting Docker Poller")
        while True:
            async for containers in self.poll():
                self.logger.debug("Called docker poller")
                for container in containers:
                    if not self.is_enabled(container):
                        self.logger.debug(f"Skipping container {container.id}")
                    for host in container.hosts:
                        await SyncManager().put([host], PollerSource.DOCKER)


@deprecated("Use DockerPoller instead")
def check_container_t2(containers: list | None, settings, logger):
    mappings = {}

    client = DockerPoller(settings, logger=logger)
    if containers is None:
        containers = asyncio.run(anext(client.poll()))
    for container in containers or []:
        if client.is_enabled(container):
            for host in container.hosts:
                mappings[host] = 1

    return mappings


async def watch_events(dk_agent, settings):
    t = datetime.now().strftime("%s")

    logger.debug("Starting event watch docker events")
    logger.debug("Time: %s", t)
    forever = 0

    while forever < 777:
        logger.debug("Called docker poller")
        t_next = datetime.now().strftime("%s")
        events = dk_agent.events(
            since=t,
            until=t_next,
            filters={"Type": "service", "Action": "update", "status": "start"},
            decode=True,
        )
        new_mappings = {}
        for event in events:
            if event.get("status") == "start":
                try:
                    container = await asyncio.to_thread(dk_agent.containers.get, event.get("id"))
                    add_to_mappings(
                        new_mappings,
                        check_container_t2(container, settings, logger),
                    )
                except docker.errors.NotFound:
                    forever = 778
                    pass
        sync_mappings(settings, new_mappings, settings.domains, logger)
        t = t_next
        await asyncio.sleep(5)  # Sleep for 5 seconds before checking for new events


logger = None


def get_logger():
    return logger


async def main():
    # Load settings
    try:
        settings = Settings()  # type: ignore[call-arg]

        # Check for uppercase docker secrets or env variables
        assert settings.cf_token
        assert settings.target_domain
        assert len(settings.domains) > 0

    except ValidationError as e:
        print(f"Unable to load settings: {e}", file=sys.stderr)
        sys.exit(1)

    # Set up logging and dump runtime settings
    global logger
    logger = initialize_logger(settings)
    report_current_status_and_settings(logger, settings)

    # Init agents
    if settings.enable_docker_poll:
        dk_agent = DockerPoller(settings, logger).client

    # Init mappings
    mappings = get_initial_mappings(
        dk_agent,
        settings,
        settings.traefik_included_hosts,
        settings.traefik_excluded_hosts,
    )
    sync_mappings(settings, mappings, settings.domains)

    # Start traefik polling on a separate thread
    polls = []
    if settings.enable_traefik_poll:
        logger.debug("Starting traefik router polling")
        traefik_poll = AsyncRepeatedTimer(
            settings.traefik_poll_seconds,
            check_traefik_and_sync_mappings,
            args=(
                settings,
                settings.traefik_included_hosts,
                settings.traefik_excluded_hosts,
                settings.domains,
                logger,
            ),
        )
        polls.append(traefik_poll.start())

    # Start docker polleer
    if settings.enable_docker_poll:
        docker_poll = asyncio.create_task(watch_events(dk_agent, settings))
        polls.append(docker_poll)

    # Run pollers in parallel
    try:
        await asyncio.gather(*polls)
    except asyncio.CancelledError:
        logger.info("Pollers were stopped...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Exiting...")
        for task in asyncio.all_tasks():
            task.cancel()
        sys.exit(0)
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        sys.exit(1)
