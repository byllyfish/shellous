"Implementation for harvest and harvest_results function."

import asyncio

from shellous.log import LOG_DETAIL, LOGGER

_CANCEL_TIMEOUT = 15.0  # seconds to wait for cancelled task to finish


async def harvest(
    *aws,
    timeout=None,
    cancel_timeout=_CANCEL_TIMEOUT,
    trustee=None,
    cancel_finish=False,
):
    """Run a bunch of awaitables as tasks. Do not return results.

    After the harvest returns, all of the awaitables are guaranteed to be done.

    Raises first exception seen, or just returns normally.

    Similar to `asyncio.gather` but doesn't return anything.
    If an awaitable raises an exception, the other awaitables are immediately
    cancelled and consumed before raising the first exception seen.

    Set `timeout` to specify a timeout in seconds. When the timeout expires,
    all awaitables are cancelled and consumed before raising a
    `asyncio.TimeoutError`.

    If `harvest` is cancelled itself, all awaitables are cancelled and
    consumed before raising `CancelledError`. If `cancel_finish` is True, the
    tasks are not cancelled, but allowed to finish.
    """

    tasks = [asyncio.ensure_future(item) for item in aws]
    await harvest_wait(
        tasks,
        timeout=timeout,
        cancel_timeout=cancel_timeout,
        cancel_finish=cancel_finish,
        trustee=trustee,
    )
    _consume_exceptions(tasks)
    for task in tasks:
        if not task.cancelled():
            task.result()


async def harvest_results(
    *aws,
    timeout=None,
    cancel_timeout=_CANCEL_TIMEOUT,
    trustee=None,
):
    """Run a bunch of awaitables as tasks and return (cancelled, results).

    ```
    cancelled, results = harvest_results(aws)
    ```

    After the harvest returns, all of the awaitables are guaranteed to be done.

    Exceptions are included in the result list, including CancelledError.

    Similar to `asyncio.gather` with `return_exceptions` with one difference:
    If an awaitable raises an exception, the other awaitables are immediately
    cancelled and consumed before returning any results.

    Set `timeout` to specify a timeout in seconds. When the timeout expires,
    all awaitables are cancelled and consumed before raising a
    `asyncio.TimeoutError`.

    If `harvest_results` is cancelled itself, all awaitables are cancelled and
    `cancelled` is returned as True.
    """

    tasks = [asyncio.ensure_future(item) for item in aws]
    cancelled = False
    try:
        await harvest_wait(
            tasks,
            timeout=timeout,
            cancel_timeout=cancel_timeout,
            trustee=trustee,
        )
    except asyncio.CancelledError:
        cancelled = True
    return cancelled, [_to_result(task) for task in tasks]


async def harvest_wait(
    tasks,
    *,
    timeout=None,
    cancel_timeout=_CANCEL_TIMEOUT,
    cancel_finish=False,
    trustee=None,
):
    """Wait for tasks to finish or raise an exception.

    After the harvest returns, all of the tasks are guaranteed to be done.

    If there are pending tasks, they are cancelled and collected. Their
    exceptions are not consumed.

    Set `timeout` to specify a timeout in seconds. When the timeout expires,
    all tasks are cancelled and consumed before raising a
    `asyncio.TimeoutError`.

    If `harvest_wait` is cancelled itself, all tasks are cancelled and
    consumed before raising `CancelledError`. If `cancel_finish` is True,
    the tasks are not cancelled, but allowed to finish.
    """

    try:
        # Wait for all tasks to complete, the first one to raise an
        # exception, or a timeout.
        done, pending = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_EXCEPTION
        )

        # Determine whether asyncio.wait timed out.
        time_expired = False
        if not done:
            time_expired = True
        elif pending:
            # No tasks in `done` finished with an exception.
            time_expired = not any(
                task.exception() for task in done if not task.cancelled()
            )

    except asyncio.CancelledError:
        # Cancel all tasks and wait for them to finish.
        if LOG_DETAIL:
            LOGGER.info(
                "harvest_wait cancelled trustee=%r cancel_finish=%r",
                trustee,
                cancel_finish,
            )
        await _cancel_wait(tasks, trustee, cancel_timeout, cancel_finish)
        _consume_exceptions(tasks)
        raise

    if pending:
        await _cancel_wait(pending, trustee, cancel_timeout)

    assert all(task.done() for task in tasks)

    if time_expired:
        if LOG_DETAIL:
            LOGGER.info("harvest_wait timed out trustee=%r", trustee)
        _consume_exceptions(tasks)
        raise asyncio.TimeoutError()


async def _cancel_wait(tasks, trustee, cancel_timeout, cancel_finish=False):
    "Cancel tasks and wait for them to finish."
    try:
        if not cancel_finish:
            # Cancel all tasks.
            for task in tasks:
                task.cancel()

        _, pending = await asyncio.wait(
            tasks,
            timeout=cancel_timeout,
            return_when=asyncio.ALL_COMPLETED,
        )

        if pending:
            LOGGER.error(
                "harvest._cancel_wait pending=%r trustee=%r",
                pending,
                trustee,
            )
            raise RuntimeError("Harvest._cancel_wait failed")

    except asyncio.CancelledError:
        if LOG_DETAIL:
            LOGGER.warning(
                "harvest._cancel_wait cancelled itself? trustee=%r",
                trustee,
            )


def _to_result(task):
    "Return task's result, or its exception object."
    if task.cancelled():
        return asyncio.CancelledError()
    ex = task.exception()
    if ex:
        # Re-raise certain exceptions that are too important to wait.
        if isinstance(ex, (AssertionError, RuntimeError)):
            raise ex
        return ex
    return task.result()


def _consume_exceptions(tasks):
    "Consume exception for every done task to eliminate warning messages."
    for task in tasks:
        assert task.done()
        if not task.cancelled():
            task.exception()
