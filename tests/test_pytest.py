"Unit tests for using shellous commands in a pytest fixture."

import contextlib
import contextvars

import pytest

from shellous import Runner, sh


@pytest.fixture
async def echo_broken():
    """This fixture fails because pytest-asyncio doesn't preserve context vars.

    https://github.com/pytest-dev/pytest-asyncio/issues/127
    """
    async with sh("echo") as run:
        yield run


@pytest.mark.xfail(raises=RuntimeError)
async def test_echo_broken(echo_broken):
    assert echo_broken.command.args[0] == "echo"


@pytest.fixture
async def echo_workaround():
    "One work-around is to avoid the contextvar by calling Runner explicitly."
    async with Runner(sh("echo")) as run:
        yield run


async def test_echo_workaround(echo_workaround):
    assert echo_workaround.command.args[0] == "echo"


@contextlib.contextmanager
def _preserve_contextvars():
    "Context manager that copies the `context_stack` context var."
    old_context = contextvars.copy_context()
    yield
    new_context = contextvars.copy_context()
    for var in old_context:
        if var.name.startswith("shellous.") and var not in new_context:
            var.set(old_context[var])


@pytest.fixture
async def echo_preserved():
    "Another work-around explicitly copies the contextvars (a bit hacky)."
    async with sh("echo") as run:
        with _preserve_contextvars():
            yield run


async def test_echo_preserved(echo_preserved):
    assert echo_preserved.command.args[0] == "echo"
