import asyncio
import sys

import pytest


@pytest.fixture(autouse=True)
async def yield_time():
    """Yield time to clean up before pytest closes event loop.
    Sometimes needed to clean up properly after cancelled tasks."""
    yield
    await asyncio.sleep(0.00001)


@pytest.fixture(autouse=True)
async def yield_time_win32():
    """Yield time to clean up before pytest closes event loop.
    Sometimes needed to clean up properly after cancelled tasks."""
    yield
    if sys.platform == "win32":
        await asyncio.sleep(0.00001)
