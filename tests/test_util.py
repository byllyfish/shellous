import asyncio

import pytest
from shellous.util import gather_collect

pytestmark = pytest.mark.asyncio


async def test_gather_collect():
    "Test the `gather_collect` function."
    some_list = []

    async def _coro1():
        await asyncio.sleep(0.001)
        raise ValueError(7)

    async def _coro2(obj):
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)
            obj.append(1)

    with pytest.raises(ValueError, match="7"):
        await gather_collect(_coro1(), _coro2(some_list))

    # Test that `some_list` is modified as a side-effect of cancelling _coro2.
    assert some_list == [1]

    # There should only be one task in this event loop.
    tasks = asyncio.all_tasks()
    assert len(tasks) == 1 and tasks.pop() is asyncio.current_task()


async def test_gather_collect_cancel():
    "Test the `gather_collect` function cancellation behavior."

    async def _coro():
        await asyncio.sleep(60.0)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(gather_collect(_coro(), _coro()), 0.1)


async def test_gather_collect_return_exceptions():
    "Test the `gather_collect` function."

    async def _coro1():
        await asyncio.sleep(0.001)
        raise ValueError(7)

    async def _coro2():
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            pass
        return 99

    result = await gather_collect(_coro1(), _coro2(), return_exceptions=True)

    assert isinstance(result[0], ValueError)
    assert result[0].args[0] == 7
    assert result[1] == 99


async def test_gather_collect_timeout():
    "Test the `gather_collect` function with a timeout."

    async def _coro():
        await asyncio.sleep(60.0)

    with pytest.raises(asyncio.TimeoutError):
        await gather_collect(_coro(), _coro(), timeout=0.1)
