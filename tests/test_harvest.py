"Unit tests for harvest() function."

import asyncio

import pytest
from shellous.harvest import harvest, harvest_results, harvest_wait

pytestmark = pytest.mark.asyncio


async def test_harvest():
    "Test the `harvest` function."
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
        await harvest(_coro1(), _coro2(some_list))

    # Test that `some_list` is modified as a side-effect of cancelling _coro2.
    assert some_list == [1]


async def test_harvest_cancel():
    "Test the `harvest` function cancellation behavior."

    async def _coro():
        await asyncio.sleep(60.0)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(harvest(_coro(), _coro()), 0.1)


async def test_harvest_results():
    "Test the `harvest` function."

    async def _coro1():
        await asyncio.sleep(0.001)
        raise ValueError(7)

    async def _coro2():
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            pass
        return 99

    cancelled, result = await harvest_results(_coro1(), _coro2())

    assert not cancelled
    assert isinstance(result[0], ValueError)
    assert result[0].args[0] == 7
    assert result[1] == 99


async def test_harvest_timeout():
    "Test the `harvest` function with a timeout."

    async def _coro():
        await asyncio.sleep(60.0)

    with pytest.raises(asyncio.TimeoutError):
        await harvest(_coro(), _coro(), timeout=0.1)


async def test_harvest_cancel_subtask():
    """Test `harvest` function when one subtask is cancelled."""

    async def coro():
        await asyncio.sleep(1.0)
        raise ValueError(1)

    task1 = asyncio.create_task(coro())
    htask = asyncio.create_task(harvest(task1, coro(), coro()))

    task1.cancel()
    with pytest.raises(ValueError):
        await htask


async def test_harvest_2_done_tasks():
    """Test `harvest` function when one subtask is cancelled and
    another raises an exception."""

    async def coro1():
        await asyncio.sleep(0.25)
        raise ValueError(1)

    async def coro2():
        await asyncio.sleep(1.0)

    task1 = asyncio.create_task(coro2())
    htask = asyncio.create_task(harvest(task1, coro1(), coro2()))

    task1.cancel()
    with pytest.raises(ValueError):
        await htask


async def test_harvest_wait_timeout():
    "Test the harvest_wait function."

    async def coro():
        await asyncio.sleep(30)

    tasks = [asyncio.create_task(coro())]

    with pytest.raises(asyncio.TimeoutError):
        await harvest_wait(tasks, timeout=0.1)

    assert tasks[0].done()
    assert tasks[0].cancelled()


async def test_harvest_wait_cancel_finish():
    "Test the harvest_wait function with cancel_finish=True."

    _finished = False

    async def _coro1():
        nonlocal _finished
        await asyncio.sleep(0.1)
        _finished = True

    async def _harvest():
        tasks = [asyncio.create_task(_coro1())]
        await harvest_wait(tasks, cancel_finish=True)

    task = asyncio.create_task(_harvest())

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _finished


async def test_harvest_cancel_finish():
    "Test the harvest function with cancel_finish=True."

    _finished = False

    async def _coro1():
        nonlocal _finished
        await asyncio.sleep(0.1)
        _finished = True

    async def _harvest():
        await harvest(_coro1(), cancel_finish=True)

    task = asyncio.create_task(_harvest())

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _finished


async def test_harvest_cancel_failed():
    "Test the harvest function with a task that refuses to cancel."

    async def _coro():
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(60.0)

    with pytest.raises(RuntimeError, match="_cancel_waiter failed"):
        await harvest(_coro(), timeout=0.2, cancel_timeout=0.2)

    # Give cancelled task a chance to exit.
    await asyncio.sleep(0.001)


async def test_harvest_cancel_wait_cancelled():
    "Test the harvest function when _cancel_wait is itself cancelled."

    async def _coro():
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(60.0)

    async def _task1():
        await harvest(_coro(), timeout=0.2, cancel_timeout=0.5)

    task = asyncio.create_task(_task1())
    await asyncio.sleep(0.4)
    task.cancel()

    with pytest.raises(RuntimeError, match="_cancel_waiter failed"):
        await task

    # Give cancelled task a chance to exit.
    await asyncio.sleep(0.001)
