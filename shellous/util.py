"Implements various utility functions."

import asyncio
import os
import platform
from typing import Optional, Union


def decode(data: Optional[bytes], encoding: Optional[str]) -> Union[str, bytes]:
    "Utility function to decode optional byte strings."
    if encoding is None:
        if data is None:
            return b""
        return data
    if not data:
        return ""
    return data.decode(*encoding.split(maxsplit=1))


def coerce_env(env: dict):
    """Utility function to coerce environment variables to string.

    If the value of an environment variable is `...`, grab the value from the
    parent environment.
    """

    def _coerce(key, value):
        if value is ...:
            value = os.environ[key]
        return str(value)

    return {str(key): _coerce(key, value) for key, value in env.items()}


def platform_info():
    "Return platform information for use in logging."

    platform_vers = platform.platform(terse=True)
    python_impl = platform.python_implementation()
    python_vers = platform.python_version()

    # Include module name with name of loop class.
    loop_cls = asyncio.get_running_loop().__class__
    loop_name = f"{loop_cls.__module__}.{loop_cls.__name__}"

    try:
        # Child watcher is only implemented on Unix.
        child_watcher = asyncio.get_child_watcher().__class__.__name__
    except NotImplementedError:
        child_watcher = None

    info = f"{platform_vers} {python_impl} {python_vers} {loop_name}"
    if child_watcher:
        return f"{info} {child_watcher}"
    return info


def close_fds(open_fds):
    "Close open file descriptors or file objects."
    try:
        for obj in open_fds:
            if isinstance(obj, int):
                if obj >= 0:
                    try:
                        os.close(obj)
                    except OSError:
                        pass
            else:
                obj.close()
    finally:
        open_fds.clear()
