"Implementation for harvest and harvest_results function."

import asyncio

from shellous.log import LOGGER


async def harvest(*aws, timeout=None, trustee=None):
    """Run a bunch of awaitables as tasks. Does not return results.

    Raises first exception seen.

    Similar to `asyncio.gather` but doesn't return anything.
    If an awaitable raises an exception, the other awaitables are immediately
    cancelled and consumed before raising this first exception.

    Set `timeout` to specify a timeout in seconds. When the timeout expires,
    all awaitables are cancelled and consumed before raising a
    `asyncio.TimeoutError`.

    If `harvest_results` is cancelled itself, all awaitables are cancelled and
    consumed before raising CancelledError.
    """

    if timeout:
        await asyncio.wait_for(_harvest(aws, False, trustee), timeout)
    else:
        await _harvest(aws, False, trustee)


async def harvest_results(*aws, timeout=None, trustee=None):
    """Run a bunch of awaitables as tasks and return the results.

    Exceptions are included in the result list, including CancelledError.

    Similar to `asyncio.gather` with `return_exceptions` with one difference:
    If an awaitable raises an exception, the other awaitables are immediately
    cancelled and consumed before returning any results.

    Set `timeout` to specify a timeout in seconds. When the timeout expires,
    all awaitables are cancelled and consumed before raising a
    `asyncio.TimeoutError`.

    If `harvest_results` is cancelled itself, all awaitables are cancelled and
    consumed before raising CancelledError.
    """

    if timeout:
        return await asyncio.wait_for(_harvest(aws, True, trustee), timeout)

    return await _harvest(aws, True, trustee)


async def harvest_wait(tasks, *, timeout=None, trustee=None):
    "Helper for harvest."

    try:
        # Wait for all tasks to complete, the first one to raise an
        # exception, or a timeout. When a timeout occurs, `done` will
        # be empty.
        done, pending = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_EXCEPTION
        )
        time_expired = not done

    except asyncio.CancelledError:
        # Our own task has been cancelled.
        LOGGER.info("_harvest_wait cancelled trustee=%r", trustee)
        # Cancel all tasks and wait for them to finish.
        await _cancel_wait(tasks, trustee)
        _consume_exceptions(tasks)
        raise

    if pending:
        await _cancel_wait(pending, trustee)

    assert all(task.done() for task in tasks)

    if time_expired:
        LOGGER.info("_harvest_wait timed out trustee=%r", trustee)
        _consume_exceptions(tasks)
        raise asyncio.TimeoutError()


async def _harvest(aws, return_exceptions, trustee):
    """Helper function for harvest.

    Similar to `asyncio.gather` with one difference: If an awaitable raises
    an exception, the other awaitables are cancelled and collected before
    passing the exception to the client.

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
            "_harvest itself cancelled ret_ex=%r trustee=%r",
            return_exceptions,
            trustee,
        )
        # Cancel all tasks and wait for them to finish.
        await _cancel_wait(tasks, trustee)
        _consume_exceptions(tasks)
        raise

    # Check if all tasks are done.
    if len(done) == len(tasks):
        LOGGER.info("_harvest done %d tasks trustee=%r", len(tasks), trustee)
        assert not pending
        if return_exceptions:
            return [_to_result(task) for task in tasks]
        _consume_exceptions(tasks)
        for task in tasks:
            task.result()
        return

    LOGGER.info(
        "_harvest cancelling %d of %d tasks ret_ex=%r trustee=%r",
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
            "_harvest all tasks cancelled! done=%r pending=%r trustee=%r",
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


def _to_result(task):
    if task.cancelled():
        return asyncio.CancelledError()
    return task.exception() or task.result()


def _consume_exceptions(tasks):
    for task in tasks:
        assert task.done()
        if not task.cancelled():
            task.exception()
