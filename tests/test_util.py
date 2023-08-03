"Unit tests for functions in util module."

import asyncio
import contextlib

import pytest

from shellous.util import (
    EnvironmentDict,
    close_fds,
    coerce_env,
    context_aenter,
    context_aexit,
    decode_bytes,
    uninterrupted,
    verify_dev_fd,
)


def test_decode_bytes():
    "Test the util.decode_bytes function."
    assert decode_bytes(b"", "utf-8") == ""
    assert decode_bytes(b"abc", "utf-8") == "abc"

    with pytest.raises(AttributeError):
        assert (
            decode_bytes(None, "utf-8")  # pyright: ignore[reportGeneralTypeIssues]
            == ""
        )

    with pytest.raises(UnicodeDecodeError):
        decode_bytes(b"\x81", "utf-8")

    assert decode_bytes(b"\x81abc", "utf-8 replace") == "\ufffdabc"


def test_coerce_env():
    "Test the util.coerce_env function."
    result = coerce_env(dict(a="a", b=1))
    assert result == {"a": "a", "b": "1"}


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

    task = asyncio.create_task(task1())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(task, 0.1)

    assert task.cancelled()
    assert done


def test_verify_dev_fd():
    "Test verify_dev_fd utility function with bogus fd."
    with pytest.raises(RuntimeError, match="fdescfs"):
        verify_dev_fd(999)


def test_environment_dict():
    "Test the EnvironmentDict class."
    d1 = EnvironmentDict(None, {"a": 1})
    assert len(d1) == 1
    assert d1["a"] == "1"
    assert [x for x in d1] == ["a"]
    assert {(k, v) for k, v in d1.items()} == {("a", "1")}
    assert list(d1.keys()) == ["a"]
    assert list(d1.values()) == ["1"]
    assert repr(d1) == "{'a': '1'}"

    d2 = EnvironmentDict(None, {"b": 2})
    d3 = EnvironmentDict(None, {"a": 1})

    assert d1 != d2
    assert d1 == d3
    assert d1 == {"a": "1"}

    assert hash(d1) == hash(d3)
    assert hash(d1) != hash(d2)

    d4 = EnvironmentDict(d1, {"c": 3})
    assert d1 == {"a": "1"}
    assert d4 == {"a": "1", "c": "3"}

    # EnvironmentDict is immutable.
    with pytest.raises(TypeError):
        d1["b"] = "2"  # pyright: ignore[reportGeneralTypeIssues]


class _TestContextHelpers:
    def __init__(self):
        self.idx = 0
        self.log = []

    async def __aenter__(self):
        self.idx += 1
        return await context_aenter(self, self._ctxt(self.idx))

    async def __aexit__(self, *args):
        return await context_aexit(self, *args)

    @contextlib.asynccontextmanager
    async def _ctxt(self, idx):
        self.log.append(f"enter {idx}")
        yield self
        self.log.append(f"exit {idx}")


async def test_context_helpers_reentrant():
    """Test context manager helper function re-entrancy."""
    tc = _TestContextHelpers()

    async with tc:
        async with tc:
            async with tc:
                pass

    assert tc.log == ["enter 1", "enter 2", "enter 3", "exit 3", "exit 2", "exit 1"]


async def test_context_helpers_overlapping_tasks():
    """Test context manager helper functions are re-entrant even in overlapping
    tasks."""
    tc = _TestContextHelpers()

    async def _task():
        async with tc:
            await asyncio.sleep(0.2)

    async with tc:
        task1 = asyncio.create_task(_task())
        await asyncio.sleep(0.01)
    await task1

    # Note that child task outlives its parent task's context manager.
    # We are storing state in a contextvar, which is shared between parent
    # task and child task.
    assert tc.log == ["enter 1", "enter 2", "exit 1", "exit 2"]
