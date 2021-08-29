import logging
import os

import pytest
from shellous.util import close_fds, coerce_env, decode, log_method

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


def test_decode():
    "Test the util.decode function."
    assert decode(None, None) == b""
    assert decode(b"", None) == b""
    assert decode(b"abc", None) == b"abc"

    assert decode(b"", "utf-8") == ""
    assert decode(b"abc", "utf-8") == "abc"
    assert decode(None, "utf-8") == ""

    with pytest.raises(UnicodeDecodeError):
        decode(b"\x81", "utf-8")

    assert decode(b"\x81abc", "utf-8 replace") == "\ufffdabc"


def test_coerce_env():
    "Test the util.coerce_env function."
    result = coerce_env(dict(a="a", b=1))
    assert result == {"a": "a", "b": "1"}

    # Test on Windows using SystemRoot env, otherwise test with PATH.
    if "SystemRoot" in os.environ:
        result = coerce_env(dict(SystemRoot=...))
        assert result == {"SystemRoot": os.environ["SystemRoot"]}
    else:
        result = coerce_env(dict(PATH=...))
        assert result == {"PATH": os.environ["PATH"]}


def test_close_fds(tmp_path):
    "Test the close_fds() function."
    out = tmp_path / "test_close_fds"

    with open(out, "w") as fp:
        fd = fp.fileno()
        open_files = [fp, fd]

    close_fds(open_files)
    assert not open_files
