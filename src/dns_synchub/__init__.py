from dns_synchub.__about__ import __version__ as VERSION
from dns_synchub.logger import get_default_logger, set_default_logger
from dns_synchub.settings import Settings

__version__ = VERSION

__all__ = [
    # logger subpackage
    'get_default_logger',
    'set_default_logger',
    # settings subpackage
    'Settings',
]


def __dir__() -> list[str]:
    return sorted(__all__)
