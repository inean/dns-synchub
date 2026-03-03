import builtins
import runpy
import types
from collections.abc import Generator
from typing import Any, cast

import pytest

import dns_synchub.__main__ as main_mod


@pytest.fixture(autouse=True)
def reset_cli_module() -> Generator[None, None, None]:
    original = __import__('sys').modules.pop('dns_synchub_cli.cli', None)
    try:
        yield
    finally:
        if original is not None:
            __import__('sys').modules['dns_synchub_cli.cli'] = original


def test_main_returns_1_when_cli_package_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == 'dns_synchub_cli.cli':
            raise ImportError('missing cli')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    assert main_mod.main() == 1


def test_main_delegates_to_cli_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = types.ModuleType('dns_synchub_cli.cli')
    cast(Any, fake_module).cli = lambda: 7
    monkeypatch.setitem(__import__('sys').modules, 'dns_synchub_cli.cli', fake_module)
    assert main_mod.main() == 7


def test_module_entrypoint_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = types.ModuleType('dns_synchub_cli.cli')
    cast(Any, fake_module).cli = lambda: 0
    monkeypatch.setitem(__import__('sys').modules, 'dns_synchub_cli.cli', fake_module)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module('dns_synchub.__main__', run_name='__main__')
    assert exc.value.code == 0
