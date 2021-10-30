"Unit tests for functions in util module."

import asyncio
import os
import sys

import pytest
from shellous.util import (
    close_fds,
    coerce_env,
    decode,
    uninterrupted,
    verify_dev_fd,
    wait_pid,
)

pytestmark = pytest.mark.asyncio


def test_decode():
    "Test the util.decode function."

    assert decode(b"", "utf-8") == ""
    assert decode(b"abc", "utf-8") == "abc"
    assert decode(None, "utf-8") == ""

    with pytest.raises(UnicodeDecodeError):
        decode(b"\x81", "utf-8")

    assert decode(b"\x81abc", "utf-8 replace") == "\ufffdabc"


def test_decode_encoding_none():
    "Test the util.decode function encoding=None (invalid)."

    # Invalid but allowed.
    assert decode(None, None) == ""

    with pytest.raises(AttributeError):
        decode(b"abc", None)


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

    with open(out, "wb") as fp:
        fd = fp.fileno()
        open_files = [fp, fd]

    close_fds(open_files)
    assert not open_files


async def test_uninterrupted():
    "Test the uninterrupted() helper."

    done = False

    async def _test1():
        nonlocal done
        await asyncio.sleep(0.5)
        done = True

    async def task1():
        return await uninterrupted(_test1())

    with pytest.raises(asyncio.TimeoutError):
        task = asyncio.create_task(task1())
        await asyncio.wait_for(task, 0.1)

    assert task.cancelled()
    assert done


@pytest.mark.skipif(sys.platform == "win32", reason="Windows")
def test_wait_pid(caplog):
    "Test wait_pid utility function with bogus pid."

    result = wait_pid(os.getpid())

    assert result == 255
    assert "ChildProcessError" in caplog.record_tuples[0][2]


def test_verify_dev_fd():
    "Test verify_dev_fd utility function with bogus fd."

    with pytest.raises(RuntimeError, match="fdescfs"):
        verify_dev_fd(999)
