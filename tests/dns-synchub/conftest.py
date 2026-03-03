import re
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest


class MockDockerEvents:
    def __init__(self, data: list[dict[str, str]]):
        self.data = data
        self.close = MagicMock()
        self.reset()

    def __iter__(self) -> 'MockDockerEvents':
        return self

    def __next__(self) -> dict[str, str]:
        try:
            return next(self.iter)
        except StopIteration:
            import docker.errors

            raise docker.errors.NotFound('No more events')

    def reset(self) -> None:
        self.iter = iter(self.data)


@pytest.fixture
def docker_events_factory() -> Callable[[list[dict[str, str]]], MockDockerEvents]:
    def factory(data: list[dict[str, str]]) -> MockDockerEvents:
        return MockDockerEvents(data)

    return factory


@pytest.fixture
def docker_get_side_effect_factory() -> Callable[[dict[str, Any]], Any]:
    def factory(containers: dict[str, Any]) -> Any:
        def side_effect(url: str, *args: Any, **kwargs: dict[str, Any]) -> MagicMock:
            return_value: dict[str, Any] | list[dict[str, Any]] | None = None
            # Process URLs
            match urlparse(url).path:
                case '/version':
                    return_value = {'ApiVersion': '1.41'}
                case '/v1.41/info':
                    return_value = {'Name': 'Mock Docker'}
                case '/v1.41/containers/json':
                    return_value = [{'Id': id_} for id_ in containers.keys()]
                case details if match := re.search(r'/v1.41/containers/([^/]+)/json', details):
                    return_value = containers[match.group(1)]
                case other:
                    raise AssertionError(f'Unexpected URL: {other}')

            # Create a MagicMock object to mock the response
            response = MagicMock()
            response.json.return_value = return_value
            return response

        return side_effect

    return factory
