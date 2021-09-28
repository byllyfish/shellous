import os
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


def _is_uvloop():
    "Return true if we're running under uvloop."
    return os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop"


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
        # Work-around Windows UnicodeEncodeError: '_winapi.CreateNamedPipe' evt.
        print(event.encode("ascii", "backslashreplace").decode("ascii"))

    # Check for my audit event.
    assert any(
        event.startswith("('byllyfish/shellous.subprocess_spawn',") for event in events
    )

    if not _is_uvloop():
        # uvloop doesn't implement audit hooks.
        assert any(event.startswith("('subprocess.Popen',") for event in events)


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_audit_block_popen():
    "Test PEP 578 audit hooks."

    global _HOOK

    def _hook(event, args):
        if event == "subprocess.Popen":
            raise RuntimeError("Popen blocked")

    try:
        _HOOK = _hook

        sh = shellous.context()
        with pytest.raises(RuntimeError, match="Popen blocked"):
            await sh(sys.executable, "-c", "print('hello')")

    finally:
        _HOOK = None
