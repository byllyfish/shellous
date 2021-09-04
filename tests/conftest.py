"Configure common fixtures for pytest."

import asyncio
import contextlib
import gc
import os
import sys
import threading

import pytest

childwatcher_type = os.environ.get("SHELLOUS_CHILDWATCHER_TYPE")
loop_type = os.environ.get("SHELLOUS_LOOP_TYPE")

if loop_type:
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

    else:
        raise NotImplementedError

else:

    @pytest.fixture
    def event_loop():
        _init_child_watcher()
        loop = asyncio.new_event_loop()
        loop.set_debug(True)
        yield loop
        loop.close()
        # Force garbage collection to flush out un-run __del__ methods.
        del loop
        gc.collect()


def _init_child_watcher():
    if childwatcher_type == "fast":
        asyncio.set_child_watcher(asyncio.FastChildWatcher())
    elif childwatcher_type == "safe":
        asyncio.set_child_watcher(asyncio.SafeChildWatcher())
    elif childwatcher_type == "pidfd":
        asyncio.set_child_watcher(asyncio.PidfdChildWatcher())


@pytest.fixture(autouse=True)
async def report_orphan_tasks():
    "Make sure that all async tests exit with only a single task running."

    # Only run asyncio tests on the main thread. There may be limitations on
    # the childwatcher.
    assert threading.current_thread() is threading.main_thread()

    with _check_open_fds():
        yield

    # Check if any tasks are still running.
    tasks = asyncio.all_tasks()
    if len(tasks) > 1:
        extra_tasks = tasks - {asyncio.current_task()}
        pytest.fail(f"Orphan tasks still running: {extra_tasks}")

    # We expect the only task to be the current task.
    assert tasks.pop() is asyncio.current_task()

    # Garbage collect here to flush out warnings from __del__ methods
    # while loop is still running.
    gc.collect()
    for _ in range(3):
        await asyncio.sleep(0)


@contextlib.contextmanager
def _check_open_fds():
    "Check for growth in number of open file descriptors."
    initial_count = _count_fds()
    yield
    final_count = _count_fds()
    assert final_count == initial_count


def _count_fds():
    "Return number of open file descriptors. (Not implemented on Windows)."
    if sys.platform == "win32":
        return 0
    return len(os.listdir("/dev/fd"))
