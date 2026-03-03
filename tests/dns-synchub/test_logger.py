# pyright: reportPrivateUsage=false

import logging
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

import dns_synchub.logger as logmod
from dns_synchub.settings import Settings
from dns_synchub.settings.types import LogHandlerType
from dns_synchub.utils._once import Once


@pytest.fixture(autouse=True)
def reset_logger_once() -> None:
    logmod._logger_once = Once()


def test_console_log_handler_supports_stdout_and_stderr() -> None:
    formatter = logging.Formatter('%(message)s')
    stdout_h = logmod._console_log_handler('stdout', formatter=formatter)
    stderr_h = logmod._console_log_handler('stderr', formatter=formatter)
    assert isinstance(stdout_h, logging.StreamHandler)
    assert isinstance(stderr_h, logging.StreamHandler)

    with pytest.raises(ValueError, match='Invalid console log handler'):
        logmod._console_log_handler('invalid', formatter=formatter)


def test_file_log_handler_wraps_oserror() -> None:
    formatter = logging.Formatter('%(message)s')
    with patch('logging.FileHandler', side_effect=OSError(2, 'No such file', '/tmp/x.log')):
        with pytest.raises(RuntimeError, match='Could not open log file'):
            logmod._file_log_handler('/tmp/x.log', formatter=formatter)


def test_initialize_logger_configures_requested_handlers(tmp_path: Path) -> None:
    settings = Settings(
        dry_run=True,
        cf_token='token',
        log_handlers={LogHandlerType.STDOUT, LogHandlerType.FILE},
        log_file=str(tmp_path / 'dns-synchub.log'),
    )
    logger = logging.getLogger('dns-synchub.test.logger')
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.WARNING)

    with patch.object(logmod, 'telemetry_logger', return_value=logging.NullHandler()):
        configured = logmod.initialize_logger(logger, settings=settings)

    assert configured is logger
    assert logger.level == settings.log_level
    assert len(logger.handlers) >= 2


def test_set_default_logger_initializes_once() -> None:
    settings = Settings(dry_run=True, cf_token='token')
    logger = cast(logging.Logger, MagicMock(spec=logging.Logger))
    setup = MagicMock(return_value=logger)

    first = logmod.set_default_logger(logger, settings=settings, setup_func=setup)
    second = logmod.set_default_logger(logger, settings=settings, setup_func=setup)

    assert first is second


def test_telemetry_logger_otlp_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('OTEL_EXPORTER_OTLP_ENDPOINT', raising=False)
    with pytest.raises(ValueError, match='OTEL_EXPORTER_OTLP_ENDPOINT'):
        logmod._telemetry_logger('svc', exporters={'otlp'})
