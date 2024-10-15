import argparse
import asyncio
import logging
import os
import re
import sys
from dataclasses import MISSING, dataclass, field, fields
from logging import Logger
from typing import Any

import dotenv

import dns_synchub.logger as logger
import dns_synchub.settings as settings
from dns_synchub.__about__ import __version__ as VERSION
from dns_synchub.mappers import Mapper
from dns_synchub.pollers import Poller
from dns_synchub.settings import Settings


@dataclass
class Args:
    version: str = field(
        metadata={
            'help': 'Show program version',
            'action': 'version',
            'version': f'%(prog)s {VERSION}',
        }
    )
    env_file: str = field(metadata={'help': 'Path to the .env file', 'type': str})
    dry_run: bool = field(default=False, metadata={'help': 'Dry run mode', 'action': 'store_true'})


def parse_args() -> Args:
    parser = argparse.ArgumentParser(description='DNS SyncHub')

    # Iterate over the fields of the Args dataclass to populate the parser
    for field_info in fields(Args):
        name = field_info.name
        metadata = field_info.metadata
        default = field_info.default if field_info.default is not MISSING else None
        # Add the argument to the parser
        parser.add_argument(f'--{name.replace("_", "-")}', default=default, **metadata)

    args = parser.parse_args()

    # Load environment variables from the specified env file,
    # but only if not present in the current environment
    for key, value in dotenv.dotenv_values(args.env_file).items():
        if key not in os.environ:
            os.environ[key] = value if value else ''

    # Return an instance of the custom TypedDict
    return Args(**vars(args))


def show_config(settings: settings.Settings) -> logging.Logger:
    log = logger.set_default_logger(logging.getLogger(), settings=settings)

    settings.dry_run and log.info(f'Dry Run: {settings.dry_run}')  # type: ignore
    log.debug(f'Default TTL: {settings.default_ttl}')
    log.debug(f'Refresh Entries: {settings.refresh_entries}')

    log.debug(f"Traefik Polling Mode: {'On' if settings.enable_traefik_poll else 'Off'}")
    if settings.enable_traefik_poll:
        if settings.traefik_poll_url and re.match(r'^\w+://[^/?#]+', settings.traefik_poll_url):
            log.debug(f'Traefik Poll Url: {settings.traefik_poll_url}')
            log.debug(f'Traefik Poll Seconds: {settings.traefik_poll_seconds}')
        else:
            settings.enable_traefik_poll = False
            log.error(f'Traefik polling disabled: Bad url: {settings.traefik_poll_url}')

    log.debug(f"Docker Polling Mode: {'On' if settings.enable_docker_poll else 'Off'}")
    log.debug(f'Docker Poll Seconds: {settings.docker_timeout_seconds}')

    for dom in settings.domains:
        log.debug(f'Domain Configuration: {dom.name}')
        log.debug(f'  Target Domain: {dom.target_domain}')
        log.debug(f'  TTL: {dom.ttl}')
        log.debug(f'  Record Type: {dom.rc_type}')
        log.debug(f'  Proxied: {dom.proxied}')
        log.debug(f'  Excluded Subdomains: {dom.excluded_sub_domains}')

    return log


async def run(log: Logger, *, settings: Settings) -> None:
    # Add Cloudflarte mapper
    try:
        dns = Mapper.backends['cloudflare']
        dns = dns(log, settings=settings)
    except KeyError:
        log.error('No Cloudflare mapper found')
        return

    # Add Pollers
    pollers: list[Poller[Any]] = []
    if settings.enable_traefik_poll:
        TraefikPoller = Poller.backends['traefik']
        pollers.append(TraefikPoller(log, settings=settings))
    if settings.enable_docker_poll:
        DockerPoller = Poller.backends['docker']
        pollers.append(DockerPoller(log, settings=settings))

    # Start Pollers
    try:
        async with asyncio.TaskGroup() as tg:
            for poller in pollers:
                await poller.events.subscribe(dns)
                tg.create_task(poller.start())
    except asyncio.CancelledError:
        pass


def cli() -> int:
    try:
        # Load environment variables from the specified env file
        args = parse_args()
        # Load settings
        options = settings.Settings(dry_run=args.dry_run)
        # Check for uppercase docker secrets or env variables
        assert options.cf_token
        assert options.target_domain
        assert len(options.domains) > 0
    except settings.ValidationError as e:
        print(f'Unable to load settings: {e}', file=sys.stderr)
        return 1

    # Set up logging and dump runtime settings
    log = show_config(options)
    try:
        asyncio.run(run(log, settings=options))
    except KeyboardInterrupt:
        # asyncio.run will cancel any task pending when the main function exits
        log.info('Cancel by user.')
        log.info('Exiting...')

    # Exit grqacefully
    return 0
