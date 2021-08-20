"Configure common fixtures for pytest."

import asyncio

import pytest


@pytest.fixture(autouse=True)
async def report_orphan_tasks():
    "Make sure that all async tests exit with only a single task running."
    yield
    tasks = asyncio.all_tasks()

    if len(tasks) > 1:
        extra_tasks = tasks - {asyncio.current_task()}
        pytest.fail(f"Orphan tasks still running: {extra_tasks}")

    # We expect the only task to be the current task.
    assert tasks.pop() is asyncio.current_task()
