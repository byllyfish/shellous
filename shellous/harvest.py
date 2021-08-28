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
        # Wait for all tasks to complete, or the first one to raise an
        # exception.
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    except asyncio.CancelledError:
        # Our own task has been cancelled.
        LOGGER.info(
            "harvest itself cancelled ret_ex=%r trustee=%r",
            return_exceptions,
            trustee,
        )
        # Cancel all tasks and wait for them to finish.
        await _cancel_wait(tasks, trustee)
        _consume_exceptions(tasks)
        raise

    # Check if all tasks are done.
    if len(done) == len(tasks):
        LOGGER.info("harvest done %d tasks trustee=%r", len(tasks), trustee)
        assert not pending
        if return_exceptions:
            return [_to_result(task) for task in tasks]
        _consume_exceptions(tasks)
        return [task.result() for task in tasks]

    LOGGER.info(
        "harvest cancelling %d of %d tasks ret_ex=%r trustee=%r",
        len(pending),
        len(tasks),
        return_exceptions,
        trustee,
    )

    # Cancel pending tasks and wait for them to finish.
    await _cancel_wait(pending, trustee)

    if return_exceptions:
        # Return list of exceptions in same order as original tasks.
        return [_to_result(task) for task in tasks]

    # Consume exceptions that cancellation may have triggered.
    _consume_exceptions(tasks)

    # Look for a task that is `done` searching from first to last.
    ready = next(
        (task for task in tasks if task.done() and not task.cancelled()),
        None,
    )
    if not ready:
        # All tasks were cancelled, but the `harvest` itself was not.
        LOGGER.warning(
            "harvest all tasks cancelled! done=%r pending=%r trustee=%r",
            done,
            pending,
            trustee,
        )
        raise asyncio.CancelledError()

    assert ready.exception()
    return ready.result()


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


def _consume_exceptions(tasks):
    for task in tasks:
        assert task.done()
        if not task.cancelled():
            task.exception()
