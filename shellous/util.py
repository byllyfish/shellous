"Implements Redirect enum and various utility functions."

import asyncio
import enum
from typing import Optional, Union

from shellous.log import LOGGER


class Redirect(enum.IntEnum):
    "Redirection constants."

    STDOUT = asyncio.subprocess.STDOUT  # -2
    DEVNULL = asyncio.subprocess.DEVNULL  # -3
    CAPTURE = -10
    INHERIT = -11

    def is_custom(self):
        "Return true if this redirect option is not built into asyncio."
        return self in {Redirect.CAPTURE, Redirect.INHERIT}


def decode(data: Optional[bytes], encoding: Optional[str]) -> Union[str, bytes, None]:
    "Utility function to decode optional byte strings."
    if encoding is None or data is None:
        return data
    if " " in encoding:
        encoding, errors = encoding.split(maxsplit=1)
        return data.decode(encoding, errors)
    return data.decode(encoding)


async def gather_collect(*aws, return_exceptions=False):
    """Run a bunch of awaitables as tasks and return the results.

    Similar to `asyncio.gather` with one difference: If an awaitable raises
    an exception, the other awaitables are cancelled and collected before
    passing the exception to the client.

    When `return_exceptions` is True, this method will include exceptions in
    the list of results returned, including `asyncio.CancelError` exceptions.
    """

    tasks = [asyncio.ensure_future(item) for item in aws]

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except asyncio.CancelledError:
        LOGGER.warning("gather_collect itself cancelled")
        # `asyncio.wait` does not cancel tasks when it is cancelled itself.
        # Cancel all tasks here before re-raising the exception.
        for task in tasks:
            task.cancel()
        raise

    if len(done) == len(tasks):
        return [task.result() for task in tasks]

    LOGGER.info("gather_collect cancelling %d tasks", len(pending))
    for task in pending:
        task.cancel()

    try:
        await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)
    except asyncio.CancelledError:
        LOGGER.warning("gather_collect pending cleanup cancelled")
        raise

    if return_exceptions:
        # Return list of exceptions in same order as original tasks.
        return [_to_result(task) for task in tasks]

    # Look for a task in `done` that wasn't cancelled, if there is one.
    failed = [task for task in done if not task.cancelled()]
    if failed:
        failed[0].result()

    # Only choice is a task that was cancelled.
    done.pop().result()


def _to_result(task):
    if task.cancelled():
        return asyncio.CancelledError()
    return task.exception() or task.result()
