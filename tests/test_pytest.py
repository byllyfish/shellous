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
