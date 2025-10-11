"Configure common fixtures for pytest."

import asyncio
import contextlib
import gc
import os
import platform
import re
import sys
import threading

import pytest

from shellous import sh

_PYPY = platform.python_implementation() == "PyPy"

# Close any file descriptors >= 3. The tests will log file descriptors passed
# to subprocesses. If pytest inherits file descriptors from the process that
# launches it, this perturbs the testing environment. I have seen this with
# processes launched using the VSCode Terminal.

if not os.environ.get("COVERAGE_RUN"):
    os.closerange(3, 600)

_watcher_type = os.environ.get("SHELLOUS_CHILDWATCHER_TYPE")
_loop_type = os.environ.get("SHELLOUS_LOOP_TYPE")


class _CustomEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    "Custom event loop policy for tests."

    if sys.platform != "win32" and _loop_type == "uvloop":

        def _loop_factory(self):
            import uvloop

            return uvloop.new_event_loop()

    def __init__(self):
        super().__init__()
        watcher = self._get_watcher()
        if watcher:
            self.set_child_watcher(watcher)

    def new_event_loop(self):
        "Return a new event loop."
        loop = super().new_event_loop()
        loop.set_debug(True)
        if _loop_type == "eager_task_factory":
            assert sys.version_info[0:2] >= (3, 12), "requires python 3.12"
            loop.set_task_factory(
                asyncio.eager_task_factory  # pyright: ignore[reportArgumentType]
            )
        return loop

    def _get_watcher(self):
        "Construct a watcher, if requested."
        if _watcher_type == "safe":
            return asyncio.SafeChildWatcher()
        if _watcher_type == "pidfd":
            return asyncio.PidfdChildWatcher()
        if _watcher_type is not None:
            raise NotImplementedError
        return None


@pytest.fixture
def event_loop_policy():
    return _CustomEventLoopPolicy()


@pytest.fixture(autouse=True)
async def report_orphan_tasks():
    "Make sure that all async tests exit with only a single task running."
    # Only run asyncio tests on the main thread. There may be limitations on
    # the childwatcher.
    assert threading.current_thread() is threading.main_thread()

    with _check_open_fds():
        yield

    # Check if any other tasks are still running. Ignore the current task.
    extra_tasks = asyncio.all_tasks() - {asyncio.current_task()}

    # Check if any running tasks are related to async generators. If so, yield
    # time to get them to exit and update `extra_tasks`.
    agen_tasks = {
        task
        for task in extra_tasks
        if "<async_generator_athrow without __name__>" in repr(task)
    }
    if agen_tasks:
        # Make sure to yield enough time under code coverage.
        await asyncio.sleep(0.05)
        extra_tasks = asyncio.all_tasks() - {asyncio.current_task()}

    if extra_tasks:
        pytest.fail(f"Orphan tasks still running: {extra_tasks}")

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
    if _PYPY:
        gc.collect()  # Force gc for pypy
    extra_fds = _get_fds() - initial_set
    assert not extra_fds, f"file descriptors still open: {extra_fds}"


def _get_fds():
    "Return set of open file descriptors. (Not implemented on Windows)."
    if sys.platform == "win32" or _loop_type == "uvloop":
        return set()
    return set(os.listdir("/dev/fd"))


async def _get_children():
    "Return set of child processes. (Not implemented on Windows)"
    if sys.platform == "win32":
        return set()

    ps = sh("ps", "axo", "pid=,ppid=,stat=")
    my_pid = os.getpid()

    children = set()
    async with ps as run:
        async for line in run:
            m = re.match(f"^\\s*(\\d+)\\s+{my_pid}\\s+(.*)$", line)
            if m:
                # Report child as "pid/stat"
                child_pid = int(m.group(1))
                if child_pid != run.pid:
                    children.add(f"{m.group(1)}/{m.group(2).strip()}")

    return children


@pytest.fixture(autouse=True, scope="session")
def _pytest_readline_workaround():
    # Importing `readline` has the silent side-effect of adding the 'COLUMNS'
    # and 'LINES' environment variables. In subprocesses, these can override
    # my terminal window size tests...
    # see https://github.com/pytest-dev/pytest/issues/12888

    if "readline" in sys.modules:
        os.environ["COLUMNS"] = os.environ["LINES"] = ""
        del os.environ["COLUMNS"], os.environ["LINES"]
