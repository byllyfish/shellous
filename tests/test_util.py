import asyncio
import logging

import pytest
from shellous.util import gather_collect, log_method

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


class _Tester:
    @log_method(True)
    async def demo1(self):
        pass

    @log_method(False)
    async def demo2(self):
        pass

    @log_method(True)
    async def demo3(self):
        raise ValueError(1)

    def __repr__(self):
        return "<self>"


async def test_log_method(caplog):
    "Test the log_method decorator for async methods."
    caplog.set_level(logging.INFO)

    tester = _Tester()
    await tester.demo1()
    await tester.demo2()
    with pytest.raises(ValueError):
        await tester.demo3()

    assert caplog.record_tuples == [
        ("shellous", 20, "_Tester.demo1 stepin <self>"),
        ("shellous", 20, "_Tester.demo1 stepout <self> ex=None"),
        ("shellous", 20, "_Tester.demo3 stepin <self>"),
        ("shellous", 20, "_Tester.demo3 stepout <self> ex=ValueError(1)"),
    ]
