"Implementation for harvest function, a better `asyncio.gather`."

import asyncio

from shellous.log import LOGGER


async def harvest(*aws, timeout=None, return_exceptions=False, trustee=None):
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
            _harvest(aws, return_exceptions, trustee),
            timeout,
        )
    return await _harvest(aws, return_exceptions, trustee)


async def _harvest(aws, return_exceptions, trustee):
    """Helper function for harvest.

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
        LOGGER.warning(
            "harvest itself cancelled ret_ex=%r trustee=%r",
            return_exceptions,
            trustee,
        )
        await _cancel_wait(tasks, trustee)
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
        "harvest cancelling %d of %d tasks ret_ex=%r trustee=%r",
        len(pending),
        len(tasks),
        return_exceptions,
        trustee,
    )
    await _cancel_wait(pending, trustee)

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
        "harvest all tasks cancelled! done=%r pending=%r ret_ex=%r trustee=%r",
        done,
        pending,
        return_exceptions,
        trustee,
    )
    raise asyncio.CancelledError()


async def _cancel_wait(tasks, trustee):
    "Cancel tasks and wait for them to finish."
    try:
        for task in tasks:
            task.cancel()
        _, pending = await asyncio.wait(
            tasks,
            timeout=1.0,
            return_when=asyncio.ALL_COMPLETED,
        )
        if pending:
            LOGGER.error(
                "harvest._cancel_wait pending=%r all_tasks=%r trustee=%r",
                pending,
                asyncio.all_tasks(),
                trustee,
            )
    except asyncio.CancelledError:
        LOGGER.warning(
            "harvest._cancel_wait cancelled itself? trustee=%r",
            trustee,
        )
        pass


def _to_result(task):
    if task.cancelled():
        return asyncio.CancelledError()
    return task.exception() or task.result()


def _retrieve_exceptions(tasks):
    for task in tasks:
        if not task.cancelled():
            task.exception()
