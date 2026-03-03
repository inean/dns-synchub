from dns_synchub.mappers import Mapper
from dns_synchub.pollers import Poller


def test_mapper_entrypoints_include_cloudflare() -> None:
    assert 'cloudflare' in Mapper.backends


def test_poller_entrypoints_include_docker_and_traefik() -> None:
    assert 'docker' in Poller.backends
    assert 'traefik' in Poller.backends
