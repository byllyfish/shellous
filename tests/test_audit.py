import sys

import pytest
import shellous

pytestmark = pytest.mark.asyncio

_HOOK = None
_IGNORE = {"object.__getattr__", "sys._getframe", "code.__new__", "builtins.id"}


def _audit_hook(event, args):
    if _HOOK and event not in _IGNORE:
        _HOOK(event, args)


sys.addaudithook(_audit_hook)


async def test_audit():
    "Test PEP 578 audit hooks."

    global _HOOK
    events = []

    def _hook(*info):
        events.append(repr(info))

    try:
        _HOOK = _hook
        sh = shellous.context()
        result = await sh(sys.executable, "-c", "print('hello')")
    finally:
        _HOOK = None

    assert result.rstrip() == "hello"
    for event in events:
        print(event)

    assert any(event.startswith("('subprocess.Popen',") for event in events)
