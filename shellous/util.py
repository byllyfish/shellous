"Implements various utility functions."

import asyncio
import platform
from typing import Optional, Union

from shellous.log import LOGGER


def decode(data: Optional[bytes], encoding: Optional[str]) -> Union[str, bytes, None]:
    "Utility function to decode optional byte strings."
    if encoding is None or data is None:
        return data
    if " " in encoding:
        encoding, errors = encoding.split(maxsplit=1)
        return data.decode(encoding, errors)
    return data.decode(encoding)


async def gather_collect(*aws, timeout=None, return_exceptions=False):
    """Run a bunch of awaitables as tasks and return the results.

    Similar to `asyncio.gather` with one difference: If an awaitable raises
    an exception, the other awaitables are cancelled and collected before
    passing the exception to the client. (Even if the awaitable raises a
    CancelledError...)

    Set `timeout` to specify a timeout in seconds. When the timeout expires,
    all awaitables are cancelled and collected, then we raise a
    `asyncio.TimeoutError`.

    When `return_exceptions` is True, this method will include exceptions in
    the list of results returned, including `asyncio.CancelError` exceptions.
    """
    if timeout:
        return await asyncio.wait_for(
            _gather_collect(aws, return_exceptions),
            timeout,
        )
    return await _gather_collect(aws, return_exceptions)


async def _gather_collect(aws, return_exceptions=False):
    """Helper function for gather_collect.

    Similar to `asyncio.gather` with one difference: If an awaitable raises
    an exception, the other awaitables are cancelled and collected before
    passing the exception to the client. (Even if the awaitable raises a
    CancelledError...)

    When `return_exceptions` is True, this method will include exceptions in
    the list of results returned, including `asyncio.CancelError` exceptions.
    """

    assert len(aws) > 0

    tasks = [asyncio.ensure_future(item) for item in aws]

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except asyncio.CancelledError:
        LOGGER.warning("gather_collect ret_ex=%r itself cancelled", return_exceptions)
        await _cancel_wait(tasks)
        _retrieve_exceptions(tasks)
        raise
        # done = set()
        # pending = set(tasks)

    if len(done) == len(tasks):
        if return_exceptions:
            return [_to_result(task) for task in tasks]
        _retrieve_exceptions(tasks)
        return [task.result() for task in tasks]

    LOGGER.info(
        "gather_collect ret_ex=%r cancelling %d of %d tasks",
        return_exceptions,
        len(pending),
        len(tasks),
    )
    await _cancel_wait(pending)

    if return_exceptions:
        # Return list of exceptions in same order as original tasks.
        return [_to_result(task) for task in tasks]

    # Retrieve any pending exceptions that cancellation may have triggered.
    _retrieve_exceptions(tasks)

    # Look for a task in `done` that is finished, if there is one.
    failed = [task for task in done if task.done()]
    if failed:
        failed[0].result()

    # Look for a task in `pending` that is finished, if there is one.
    failed = [task for task in pending if task.done()]
    if failed:
        failed[0].result()

    # Only choice is a task that was cancelled.
    LOGGER.warning(
        "gather_collect ret_ex=%r all tasks cancelled! done=%r pending=%r",
        return_exceptions,
        done,
        pending,
    )
    raise asyncio.CancelledError()


async def _cancel_wait(tasks):
    "Cancel tasks and wait for them to finish."
    try:
        for task in tasks:
            task.cancel()
        _, pending = await asyncio.wait(
            tasks, timeout=1.0, return_when=asyncio.ALL_COMPLETED
        )
        if pending:
            LOGGER.error(
                "gather_collect._cancel_wait pending=%r all_tasks=%r",
                pending,
                asyncio.all_tasks(),
            )
    except asyncio.CancelledError:
        LOGGER.warning("gather_collect._cancel_wait cancelled itself?")
        pass


def _to_result(task):
    if task.cancelled():
        return asyncio.CancelledError()
    return task.exception() or task.result()


def _retrieve_exceptions(tasks):
    for task in tasks:
        if not task.cancelled():
            task.exception()


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
