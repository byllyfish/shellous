"Configure common fixtures for pytest."

import asyncio
import contextlib
import gc
import logging
import os
import re
import sys
import threading

import pytest
import shellous

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
    elif childwatcher_type == "multi":
        child_watcher = asyncio.MultiLoopChildWatcher()
        logger = logging.getLogger(__name__)

        def _add_child_handler(*args):
            logger.info("MLCW.add_child_handler %r", args)
            return child_watcher.add_child_handler(*args)

        def _rmv_child_handler(*args):
            logger.info("MLCW.remove_child_handler %r", args)
            return child_watcher.remove_child_handler(*args)

        child_watcher.add_child_handler = _add_child_handler
        child_watcher.remove_child_handler = _rmv_child_handler
        asyncio.set_child_watcher(child_watcher)


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


@pytest.fixture
async def report_children():
    "Check for child processes."

    try:
        yield
    finally:
        children = await _get_children()
        if children:
            pytest.fail(f"Child processes detected: {children}")


@contextlib.contextmanager
def _check_open_fds():
    "Check for growth in number of open file descriptors."
    initial_set = _get_fds()
    yield
    extra_fds = _get_fds() - initial_set
    assert not extra_fds, f"file descriptors still open: {extra_fds}"


def _get_fds():
    "Return set of open file descriptors. (Not implemented on Windows)."
    if sys.platform == "win32" or loop_type == "uvloop":
        return set()
    return set(os.listdir("/dev/fd"))


async def _get_children():
    "Return set of child processes. (Not implemented on Windows)"
    if sys.platform == "win32":
        return set()

    sh = shellous.context()
    ps = sh("ps", "axo", "pid=,ppid=,stat=")
    my_pid = os.getpid()

    children = set()
    async with ps.run() as run:
        async for line in run:
            m = re.match(f"^\\s*(\\d+)\\s+{my_pid}\\s+(.*)$", line)
            if m:
                # Report child as "pid/stat"
                child_pid = int(m.group(1))
                if child_pid != run.pid:
                    children.add(f"{m.group(1)}/{m.group(2).strip()}")

    return children
