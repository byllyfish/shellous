"Implements various utility functions."

import io
import os
from typing import Any, Iterable, Optional, Union

from .log import LOG_DETAIL, LOGGER, log_timer


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


def wait_pid(pid: int) -> Optional[int]:
    """Call os.waitpid and return exit status.

    Return None if process is still running.
    """
    assert pid is not None and pid > 0

    try:
        result_pid, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError as ex:
        # Set status to 255 if process not found.
        LOGGER.warning("wait_pid(%r) status is 255 ex=%r", pid, ex)
        return 255

    if LOG_DETAIL:
        LOGGER.info("os.waitpid returned %r", (result_pid, status))

    if result_pid != pid:
        return None

    # Convert os.waitpid status to an exit status.
    try:
        status = os.waitstatus_to_exitcode(status)
    except ValueError:
        # See https://github.com/python/cpython/blob/7e5c107541726b90d3f2e6e69ef37180cf58335d/Lib/asyncio/unix_events.py#L47
        pass

    return status
