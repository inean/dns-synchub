from typing import Any


class classproperty(property):
    def __get__(self, owner_self: Any | None, owner_cls: type | None = None) -> Any:
        if self.fget is None:
            raise AttributeError('unreadable attribute')
        return self.fget(owner_cls)
