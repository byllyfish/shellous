"Defines package-wide logger."

import asyncio
import functools
import logging
import sys

LOGGER = logging.getLogger(__package__)


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
