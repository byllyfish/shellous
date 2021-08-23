"Configure common fixtures for pytest."

import asyncio
import gc
import os

import pytest

loop_type = os.environ.get("SHELLOUS_LOOP_TYPE")

if loop_type:
    # Customize the event loop: uvloop or SelectorEventLoop.
    if loop_type == "uvloop":
        import uvloop

        @pytest.fixture
        def event_loop():
            loop = uvloop.new_event_loop()
            loop.set_debug(True)
            yield loop
            loop.close()
            # Force garbage collection to flush out un-run __del__ methods.
            del loop
            gc.collect()

    elif loop_type == "selectoreventloop":
        import selectors

        @pytest.fixture
        def event_loop():
            selector = selectors.DefaultSelector()
            loop = asyncio.SelectorEventLoop(selector)
            loop.set_debug(True)
            yield loop
            loop.close()
            # Force garbage collection to flush out un-run __del__ methods.
            del loop
            gc.collect()

    else:

        @pytest.fixture
        def event_loop():
            loop = asyncio.new_event_loop()
            loop.set_debug(True)
            yield loop
            loop.close()
            # Force garbage collection to flush out un-run __del__ methods.
            del loop
            gc.collect()


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
