"Implements various utility functions."

import asyncio
import contextvars
import io
import os
import shutil
from asyncio.subprocess import Process
from collections import defaultdict
from typing import Any, Iterable, Optional, Union

from .log import LOG_DETAIL, LOGGER, log_timer

# Stores current stack of context managers for immutable Command objects.
_CTXT_STACK = contextvars.ContextVar("ctxt_stack", default=None)


def decode(data: Optional[bytes], encoding: str) -> str:
    "Utility function to decode optional byte strings."
    if not data:
        return ""
    return data.decode(*encoding.split(maxsplit=1))


def coerce_env(env: dict[str, Any]) -> dict[str, str]:
    """Utility function to coerce environment variables to string.

    If the value of an environment variable is `...`, grab the value from the
    parent environment.
    """

    def _coerce(key, value):
        if value is ...:
            value = os.environ[key]
        return str(value)

    return {str(key): _coerce(key, value) for key, value in env.items()}


def close_fds(open_fds: Iterable[Union[io.IOBase, int]]) -> None:
    "Close open file descriptors or file objects."
    with log_timer("close_fds"):
        try:
            for obj in open_fds:
                if isinstance(obj, int):
                    if obj >= 0:
                        try:
                            os.close(obj)
                        except OSError as ex:
                            LOGGER.warning("os.close ex=%r", ex)
                else:
                    obj.close()
        finally:
            if isinstance(open_fds, (list, set)):
                open_fds.clear()


def verify_dev_fd(fdesc: int) -> None:
    "Verify that /dev/fd file system exists and works."
    path = f"/dev/fd/{fdesc}"
    if not os.path.exists(path):
        raise RuntimeError(
            f"Missing '{path}': you may need to enable fdescfs file system"
        )


def wait_pid(pid: int, *, block: bool = False) -> Optional[int]:
    """Call os.waitpid and return exit status.

    Return None if process is still running.
    """
    assert pid > 0

    try:
        # os.WNOHANG is not available on Windows.
        options = 0 if block else os.WNOHANG  # type: ignore
        result_pid, status = os.waitpid(pid, options)
    except ChildProcessError as ex:
        # Set status to 255 if process not found.
        LOGGER.warning("wait_pid(%r) status is 255 ex=%r", pid, ex)
        return 255

    if LOG_DETAIL:
        LOGGER.info("os.waitpid(%r) returned %r", pid, (result_pid, status))

    if result_pid != pid:
        return None

    # Convert os.waitpid status to an exit status.
    try:
        status = os.waitstatus_to_exitcode(status)  # type: ignore
    except ValueError:  # pragma: no cover
        # waitstatus_to_exitcode can theoretically raise a ValueError if
        # the status is not understood. In this case, we do what
        # asyncio/unix_events.py does: return the original status.
        pass

    return status


def poll_wait_pid(proc: Process) -> bool:
    "Poll wait_pid once and return True if process has exited."
    if proc.returncode is not None:
        return True

    status = wait_pid(proc.pid)
    if status is None:
        return False

    LOGGER.debug(
        "process %r exited with returncode %r (wait_pid)",
        proc.pid,
        status,
    )

    # pylint: disable=protected-access
    proc._transport._returncode = status  # type: ignore
    proc._transport._proc.returncode = status  # type: ignore
    return True


async def uninterrupted(coro):
    "Run a coroutine so it completes even if the current task is cancelled."

    task = asyncio.create_task(coro)
    try:
        return await asyncio.shield(task)

    except asyncio.CancelledError:
        if not task.cancelled():
            await task
        raise


def which(command):
    "Given a command without a directory, return the fully qualified path."
    path = shutil.which(command)
    if path is None:
        raise FileNotFoundError(command)
    return path


async def context_aenter(scope, ctxt_manager):
    "Enter an async context manager."
    ctxt_stack = _CTXT_STACK.get()
    if ctxt_stack is None:
        ctxt_stack = defaultdict(list)
        _CTXT_STACK.set(ctxt_stack)

    result = await ctxt_manager.__aenter__()
    stack = ctxt_stack[scope]
    stack.append(ctxt_manager)

    return result


async def context_aexit(scope, exc_type, exc_value, exc_tb):
    "Exit an async context manager."
    ctxt_stack = _CTXT_STACK.get()

    stack = ctxt_stack[scope]
    ctxt_manager = stack.pop()
    if not stack:
        del ctxt_stack[scope]
    if not ctxt_stack:
        _CTXT_STACK.set(None)

    return await ctxt_manager.__aexit__(exc_type, exc_value, exc_tb)
