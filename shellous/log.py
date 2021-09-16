"Defines package-wide logger."

import asyncio
import functools
import inspect
import logging
import platform
import sys
import threading
import time
from contextlib import contextmanager

LOGGER = logging.getLogger(__package__)

# Do these at module import; may invoke subprocess.Popen.
PLATFORM_VERS = platform.platform(terse=True)
PYTHON_IMPL = platform.python_implementation()
PYTHON_VERS = platform.python_version()

LOG_IGNORE_STEPIN = -1
LOG_IGNORE_STEPOUT = -2


def _exc():
    "Return the current exception value. Useful in logging."
    return sys.exc_info()[1]


def log_method(enabled, *, _info=False, **kwds):
    """`log_method` logs when an async method call is entered and exited.

    <method-name> stepin <self>
    <method-name> stepout <self>

    If `_info` is True, include platform info in log message.

    Use `kwds` to log other arguments.
    """

    def _decorator(func):
        "Decorator to log method call entry and exit."

        if not enabled:
            return func

        is_asyncgen = inspect.isasyncgenfunction(func)
        assert is_asyncgen or inspect.iscoroutinefunction(
            func
        ), f"Expected {func.__qualname__} to be coroutine or asyncgen"

        if "." in func.__qualname__ and is_asyncgen:
            # Use _asyncgen_wrapper which includes value of `self` arg.
            @functools.wraps(func)
            async def _asyncgen_wrapper(*args, **kwargs):
                more_info, plat_info = _info_args(args, kwds, _info)

                if enabled != LOG_IGNORE_STEPIN:
                    LOGGER.info(
                        "%s stepin %r%s%s",
                        func.__qualname__,
                        args[0],
                        plat_info,
                        more_info,
                    )
                try:
                    async for i in func(*args, **kwargs):
                        yield i
                finally:
                    if enabled != LOG_IGNORE_STEPOUT:
                        LOGGER.info(
                            "%s stepout %r ex=%r%s",
                            func.__qualname__,
                            args[0],
                            _exc(),
                            more_info,
                        )

            return _asyncgen_wrapper

        if "." in func.__qualname__:
            # Use _method_wrapper which incldues value of `self` arg.
            @functools.wraps(func)
            async def _method_wrapper(*args, **kwargs):
                more_info, plat_info = _info_args(args, kwds, _info)

                if enabled != LOG_IGNORE_STEPIN:
                    LOGGER.info(
                        "%s stepin %r%s%s",
                        func.__qualname__,
                        args[0],
                        plat_info,
                        more_info,
                    )
                try:
                    return await func(*args, **kwargs)
                finally:
                    if enabled != LOG_IGNORE_STEPOUT:
                        LOGGER.info(
                            "%s stepout %r ex=%r%s",
                            func.__qualname__,
                            args[0],
                            _exc(),
                            more_info,
                        )

            return _method_wrapper

        if is_asyncgen:
            # Use _function_wrapper which ignores arguments.
            @functools.wraps(func)
            async def _asyncgen_function_wrapper(*args, **kwargs):
                if enabled != LOG_IGNORE_STEPIN:
                    LOGGER.info("%s stepin", func.__qualname__)
                try:
                    async for item in func(*args, **kwargs):
                        yield item
                finally:
                    if enabled != LOG_IGNORE_STEPOUT:
                        LOGGER.info(
                            "%s stepout ex=%r",
                            func.__qualname__,
                            _exc(),
                        )

            return _asyncgen_function_wrapper

        # Use _function_wrapper which ignores arguments.
        @functools.wraps(func)
        async def _function_wrapper(*args, **kwargs):
            if enabled != LOG_IGNORE_STEPIN:
                LOGGER.info("%s stepin", func.__qualname__)
            try:
                return await func(*args, **kwargs)
            finally:
                if enabled != LOG_IGNORE_STEPOUT:
                    LOGGER.info(
                        "%s stepout ex=%r",
                        func.__qualname__,
                        _exc(),
                    )

        return _function_wrapper

    return _decorator


def _info_args(args, kwds, _info):
    "Return info strings with specified arguments."
    more_args = [f" {key}={args[value]!r}" for key, value in kwds.items()]
    more_info = "".join(more_args)
    plat_info = f" ({_platform_info()})" if _info else ""
    return more_info, plat_info


def _platform_info():
    "Return platform information for use in logging."

    # Include module name with name of loop class.
    loop_cls = asyncio.get_running_loop().__class__
    loop_name = f"{loop_cls.__module__}.{loop_cls.__name__}"

    # Include name of current thread. If current thread is not the main thread,
    # put a "!!" in front of its name.
    current_thread = threading.current_thread()
    if current_thread is threading.main_thread():
        thread_name = current_thread.name
    else:
        thread_name = f"!!{current_thread.name}"

    try:
        # Child watcher is only implemented on Unix.
        child_watcher = asyncio.get_child_watcher().__class__.__name__
    except NotImplementedError:
        child_watcher = None

    info = f"{PLATFORM_VERS} {PYTHON_IMPL} {PYTHON_VERS} {loop_name} {thread_name}"
    if child_watcher:
        return f"{info} {child_watcher}"
    return info


@contextmanager
def log_timer(msg, warn_limit=0.1):
    "Context manager to time an operation (wall clock time)."
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        if duration >= warn_limit:
            LOGGER.warning("%s took %g seconds ex=%r", msg, duration, _exc())
