"Unit tests for PEP 578 audit hooks."

import os
import shutil
import subprocess
import sys

import pytest

from shellous import sh
from shellous.runner import EVENT_SHELLOUS_EXEC

# pylint: disable=global-statement

_HOOK = None
_IGNORE = {"object.__getattr__", "sys._getframe", "code.__new__", "builtins.id"}


def _audit_hook(event, args):
    if _HOOK and event not in _IGNORE:
        _HOOK(event, args)  # pylint: disable=not-callable


sys.addaudithook(_audit_hook)


def _is_uvloop():
    "Return true if we're running under uvloop."
    return os.environ.get("SHELLOUS_LOOP_TYPE") == "uvloop"


def _has_posix_spawn():
    return (
        not _is_uvloop()
        and sys.platform in ("darwin", "linux")
        and subprocess._USE_POSIX_SPAWN
    )


async def test_audit():
    "Test PEP 578 audit hooks."
    global _HOOK
    events = []

    def _hook(*info):
        events.append(repr(info))

    try:
        _HOOK = _hook

        result = await sh(sys.executable, "-c", "print('hello')")

    finally:
        _HOOK = None

    assert result.rstrip() == "hello"

    for event in events:
        # Work-around Windows UnicodeEncodeError: '_winapi.CreateNamedPipe' evt.
        print(event.encode("ascii", "backslashreplace").decode("ascii"))

    # Check for my audit event.
    assert any(event.startswith(f"('{EVENT_SHELLOUS_EXEC}',") for event in events)

    if not _is_uvloop():
        # uvloop doesn't implement audit hooks.
        assert any(event.startswith("('subprocess.Popen',") for event in events)

    if _has_posix_spawn() and sys.version_info < (3, 13):
        # Do not expect posix_spawn to be used on Python 3.12 or earlier.
        assert not any(event.startswith("('os.posix_spawn',") for event in events)


@pytest.mark.skipif(not _has_posix_spawn(), reason="posix_spawn")
async def test_audit_posix_spawn():
    "Test PEP 578 audit hooks."
    global _HOOK
    events = []

    def _hook(*info):
        events.append(repr(info))

    try:
        _HOOK = _hook

        # This command does not include a directory path, so it is resolved
        # through PATH.
        result = await sh("ls", "README.md").set(close_fds=False)

    finally:
        _HOOK = None

    assert result.rstrip() == "README.md"

    for event in events:
        # Work-around Windows UnicodeEncodeError: '_winapi.CreateNamedPipe' evt.
        print(event.encode("ascii", "backslashreplace").decode("ascii"))

    # Check for my audit event.
    assert any(event.startswith(f"('{EVENT_SHELLOUS_EXEC}',") for event in events)

    # Check for subprocess.Popen and os.posix_spawn.
    assert any(event.startswith("('subprocess.Popen',") for event in events)
    assert any(event.startswith("('os.posix_spawn',") for event in events)


@pytest.mark.xfail(_is_uvloop(), reason="uvloop")
async def test_audit_block_popen():
    "Test PEP 578 audit hooks."
    global _HOOK

    def _hook(event, _args):
        if event == "subprocess.Popen":
            raise RuntimeError("Popen blocked")

    try:
        _HOOK = _hook

        with pytest.raises(RuntimeError, match="Popen blocked"):
            await sh(sys.executable, "-c", "print('hello')")

    finally:
        _HOOK = None


async def test_audit_block_subprocess_spawn():
    "Test PEP 578 audit hooks."
    global _HOOK

    def _hook(event, _args):
        if event == EVENT_SHELLOUS_EXEC:
            raise RuntimeError("subprocess_spawn blocked")

    try:
        _HOOK = _hook

        cmd = sh(sys.executable, "-c", "print('hello')")
        with pytest.raises(RuntimeError, match="subprocess_spawn"):
            if sys.platform == "win32":
                # Process substitution doesn't work on Windows.
                await cmd
            else:
                # Test process substitution cleanup also.
                await cmd(cmd())

    finally:
        _HOOK = None


async def test_audit_block_pipe_specific_cmd():
    "Test PEP 578 audit hooks to block a specific command (in a pipe)."
    global _HOOK
    grep_path = shutil.which("grep")

    def _hook(event, args):
        if event == EVENT_SHELLOUS_EXEC and args[0] == grep_path:
            raise RuntimeError("grep blocked")

    callbacks = []

    def _callback(phase, info):
        runner = info["runner"]
        failure = info["failure"] or None
        exit_code = runner.returncode
        if exit_code is not None:
            exit_code = "Zero" if exit_code == 0 else "NonZero"
        callbacks.append(f"{phase}:{runner.name}:{exit_code}:{failure}")

    try:
        _HOOK = _hook

        ash = sh.set(audit_callback=_callback)
        hello = ash(sys.executable, "-c", "print('hello')").set(alt_name="hello")
        cmd = hello | ash("grep")
        with pytest.raises(RuntimeError, match="grep blocked"):
            await cmd

    finally:
        _HOOK = None

    assert callbacks in (
        [
            "start:hello:None:None",
            "start:grep:None:None",
            "stop:grep:None:RuntimeError",
            "signal:hello:None:None",
            "stop:hello:NonZero:CancelledError",
        ],
        [
            "start:hello:None:None",
            "start:grep:None:None",
            "stop:grep:None:RuntimeError",
            "stop:hello:NonZero:CancelledError",  # signal not sent to hello
        ],
        [
            "start:hello:None:None",
            "start:grep:None:None",
            "stop:grep:None:RuntimeError",
            "stop:hello:Zero:CancelledError",  # signal not sent, zero exit
        ],
        [
            "start:hello:None:None",
            "start:grep:None:None",
            "stop:grep:None:RuntimeError",
            "signal:hello:None:None",
            "stop:hello:Zero:CancelledError",
        ],
    )
