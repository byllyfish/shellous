"Unit tests for the log module."

import logging

import pytest
from shellous.log import LOG_IGNORE_STEPIN, LOG_IGNORE_STEPOUT, log_method, log_timer

pytestmark = pytest.mark.asyncio


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

    @log_method(True)
    async def demo4(self):
        for i in range(2):
            yield i

    @log_method(LOG_IGNORE_STEPOUT)
    async def demo5(self):
        pass

    @log_method(LOG_IGNORE_STEPIN)
    async def demo6(self):
        pass

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
    async for i in tester.demo4():
        pass
    await tester.demo5()
    await tester.demo6()

    assert caplog.record_tuples == [
        ("shellous", 20, "_Tester.demo1 stepin <self>"),
        ("shellous", 20, "_Tester.demo1 stepout <self> ex=None"),
        ("shellous", 20, "_Tester.demo3 stepin <self>"),
        ("shellous", 20, "_Tester.demo3 stepout <self> ex=ValueError(1)"),
        ("shellous", 20, "_Tester.demo4 stepin <self>"),
        ("shellous", 20, "_Tester.demo4 stepout <self> ex=None"),
        ("shellous", 20, "_Tester.demo5 stepin <self>"),
        ("shellous", 20, "_Tester.demo6 stepout <self> ex=None"),
    ]


def test_log_timer(caplog):
    "Test the log_timer context manager."
    caplog.set_level(logging.WARNING)

    with log_timer("test1", -1):
        pass

    assert len(caplog.record_tuples) == 1
    rec = caplog.record_tuples[0]
    assert rec[0:2] == ("shellous", 30)
    assert rec[2].startswith("test1 took ")
