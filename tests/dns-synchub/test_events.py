"""EventEmitter tests."""

from logging import Logger
from unittest.mock import AsyncMock, MagicMock

import pytest

from dns_synchub.events import EventEmitter


@pytest.mark.asyncio
async def test_event_queue_overflow_drops_oldest() -> None:
    logger = MagicMock(spec=Logger)
    emitter: EventEmitter[int] = EventEmitter(logger, origin='test', queue_maxsize=1)
    callback = AsyncMock()

    await emitter.subscribe(callback)
    emitter.set_data(1)
    emitter.set_data(2)
    await emitter.emit()

    callback.assert_awaited_once()
    assert callback.await_args is not None
    event_arg = callback.await_args.args[0]
    assert event_arg.data == 2
    logger.warning.assert_called_once()
