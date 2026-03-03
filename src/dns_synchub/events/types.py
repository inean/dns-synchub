import asyncio
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import (
    Generic,
    Protocol,
    TypeVar,
    runtime_checkable,
)

# Event Types
T = TypeVar('T')


@dataclass
class Event(Generic[T]):
    klass: type[T] = field(init=False)
    data: T

    def __post_init__(self) -> None:
        self.klass = type(self.data)


@runtime_checkable
class EventSubscriber(Protocol[T]):
    @abstractmethod
    async def __call__(self, event: Event[T]) -> None: ...


EventSubscriberCallable = Callable[[Event[T]], Awaitable[None]]
EventSubscriberType = EventSubscriberCallable[T]
EventSubscriberDataType = tuple[asyncio.Queue[Event[T]], float, float]
