"Unit tests for the log module."

import logging

import pytest
from shellous.log import log_method

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
