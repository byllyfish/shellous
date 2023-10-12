"Implements various utility functions."

import asyncio
import contextvars
import os
import sys
from asyncio.subprocess import Process
from collections import abc, defaultdict
from types import TracebackType
from typing import (
    Any,
    AsyncContextManager,
    Coroutine,
    Iterable,
    Iterator,
    Optional,
    Protocol,
    TypeVar,
    Union,
)

from .log import LOG_DETAIL, LOGGER, log_timer

_T = TypeVar("_T")
_ContextKey = tuple[int, int]

# Stores current stack of context managers for immutable Command objects.
_CONTEXT_STACKS = contextvars.ContextVar[
    dict[_ContextKey, list[AsyncContextManager[Any]]]
]("shellous.context_stacks")

# True if OS is derived from BSD.
BSD_FREEBSD = sys.platform.startswith("freebsd")
BSD_DERIVED = BSD_FREEBSD or sys.platform == "darwin"


def decode_bytes(data: bytes, encoding: str) -> str:
    "Utility function to decode byte strings."
    return data.decode(*encoding.split(maxsplit=1))


def encode_bytes(data: str, encoding: str) -> bytes:
    "Utility function to encode byte strings."
    return data.encode(*encoding.split(maxsplit=1))


def coerce_env(env: dict[str, Any]) -> dict[str, str]:
    """Utility function to coerce environment variables to string."""
    return {str(key): str(value) for key, value in env.items()}


class SupportsClose(Protocol):
    "Protocol for objects with a close() method."

    def close(self) -> None:
        "Close file object."


def close_fds(open_fds: Iterable[Union[SupportsClose, int]]) -> None:
    "Close open file descriptors or file objects."
    with log_timer("close_fds"):
        try:
            for obj in open_fds:
                if isinstance(obj, int):
                    if obj >= 0:
                        try:
                            os.close(obj)
                        except OSError as ex:
                            LOGGER.warning("os.close ex=%r", ex, stack_info=True)
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
        options = 0 if block else os.WNOHANG
        result_pid, status = os.waitpid(pid, options)
    except ChildProcessError as ex:
        # Set status to 255 if process not found.
        LOGGER.warning("wait_pid(%r) status not found (255) ex=%r", pid, ex)
        return 255

    if LOG_DETAIL:
        LOGGER.debug("os.waitpid(%r) returned %r", pid, (result_pid, status))

    if result_pid != pid:
        return None

    # Convert os.waitpid status to an exit status.
    try:
        status = os.waitstatus_to_exitcode(status)
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

    if LOG_DETAIL:
        LOGGER.debug(
            "process %r exited with returncode %r (wait_pid)",
            proc.pid,
            status,
        )

    proc._transport._returncode = status  # type: ignore
    proc._transport._proc.returncode = status  # type: ignore
    return True


async def uninterrupted(coro: Coroutine[Any, Any, _T]) -> _T:
    "Run a coroutine so it completes even if the current task is cancelled."
    task = asyncio.create_task(coro)
    try:
        return await asyncio.shield(task)

    except asyncio.CancelledError:
        if not task.cancelled():
            await task
        raise


async def context_aenter(owner: object, ctxt_manager: AsyncContextManager[_T]) -> _T:
    "Enter an async context manager."
    result = await ctxt_manager.__aenter__()  # pylint: disable=unnecessary-dunder-call

    ctxt_stacks = _CONTEXT_STACKS.get(None)
    if ctxt_stacks is None:
        ctxt_stacks = defaultdict[_ContextKey, list[Any]](list)
        _CONTEXT_STACKS.set(ctxt_stacks)

    key = (id(owner), id(asyncio.current_task()))
    stack = ctxt_stacks[key]
    stack.append(ctxt_manager)

    return result


async def context_aexit(
    owner: object,
    exc_type: Optional[type[BaseException]],
    exc_value: Optional[BaseException],
    exc_tb: Optional[TracebackType],
) -> Optional[bool]:
    "Exit an async context manager."
    try:
        ctxt_stacks = _CONTEXT_STACKS.get()
        key = (id(owner), id(asyncio.current_task()))
        stack = ctxt_stacks[key]
        ctxt_manager = stack.pop()
        if not stack:
            del ctxt_stacks[key]
    except Exception as ex:
        raise RuntimeError("context var `shellous.context_stacks` is corrupt") from ex

    return await ctxt_manager.__aexit__(exc_type, exc_value, exc_tb)


class EnvironmentDict(abc.Mapping[str, str]):
    "Read-only, hashable dictionary that stores environment variables."

    _data: dict[str, str]

    def __init__(self, base: Optional["EnvironmentDict"], updates: dict[str, Any]):
        if base is None:
            self._data = {}
        else:
            self._data = base._data.copy()
        self._data.update(**coerce_env(updates))

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __hash__(self) -> int:
        return hash(tuple((key, value) for key, value in self._data.items()))

    def __eq__(self, rhs: object) -> bool:
        if isinstance(rhs, dict):
            return self._data == rhs
        if not isinstance(rhs, EnvironmentDict):
            return False
        return self._data == rhs._data

    def __repr__(self) -> str:
        return repr(self._data)
