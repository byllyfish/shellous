"Unit tests for the log module."

import logging

import pytest

from shellous.log import LOG_DETAIL, log_method, log_timer

_LOG_LEVEL = logging.INFO if LOG_DETAIL else logging.DEBUG


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

    def __repr__(self):
        return "<self>"


async def test_log_method(caplog):
    "Test the log_method decorator for async methods."
    caplog.set_level(logging.DEBUG)

    tester = _Tester()
    await tester.demo1()
    await tester.demo2()
    with pytest.raises(ValueError):
        await tester.demo3()
    async for _ in tester.demo4():
        pass

    assert caplog.record_tuples == [
        ("shellous", _LOG_LEVEL, "_Tester.demo1 stepin <self>"),
        ("shellous", _LOG_LEVEL, "_Tester.demo1 stepout <self> ex=None"),
        ("shellous", _LOG_LEVEL, "_Tester.demo3 stepin <self>"),
        ("shellous", _LOG_LEVEL, "_Tester.demo3 stepout <self> ex=ValueError(1)"),
        ("shellous", _LOG_LEVEL, "_Tester.demo4 stepin <self>"),
        ("shellous", _LOG_LEVEL, "_Tester.demo4 stepout <self> ex=None"),
    ]


def test_log_timer(caplog):
    "Test the log_timer context manager."
    caplog.set_level(logging.DEBUG)

    with log_timer("test1", -1):
        pass

    assert len(caplog.record_tuples) == 1
    rec = caplog.record_tuples[0]
    assert rec[0:2] == ("shellous", _LOG_LEVEL)
    assert rec[2].startswith("test1 took ")
