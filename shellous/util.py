"Implements various utility functions."

import asyncio
import functools
import os
import platform
import sys
from typing import Optional, Union

from shellous.log import LOGGER


def log_method(enabled):
    """`log_method` logs when an async method call is entered and exited.

    <method-name> stepin <self>
    <method-name> stepout <self>
    """

    def _decorator(func):
        "Decorator to log method call entry and exit."

        if not enabled:
            return func

        assert asyncio.iscoroutinefunction(
            func
        ), f"Decorator expects {func.__qualname__} to be coroutine function"

        if "." in func.__qualname__:
            # Use _method_wrapper which incldues value of `self` arg.
            @functools.wraps(func)
            async def _method_wrapper(*args, **kwargs):
                LOGGER.info("%s stepin %r", func.__qualname__, args[0])
                try:
                    return await func(*args, **kwargs)
                finally:
                    LOGGER.info(
                        "%s stepout %r ex=%r",
                        func.__qualname__,
                        args[0],
                        sys.exc_info()[1],
                    )

            return _method_wrapper

        # Use _function_wrapper which ignores arguments.
        @functools.wraps(func)
        async def _function_wrapper(*args, **kwargs):
            LOGGER.info("%s stepin", func.__qualname__)
            try:
                return await func(*args, **kwargs)
            finally:
                LOGGER.info(
                    "%s stepout ex=%r",
                    func.__qualname__,
                    sys.exc_info()[1],
                )

        return _function_wrapper

    return _decorator


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
