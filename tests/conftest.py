"Configure common fixtures for pytest."

import asyncio
import contextlib
import functools
import gc
import os
import platform
import re
import signal
import sys
import threading

import pytest
import shellous
from shellous import sh

_PYPY = platform.python_implementation() == "PyPy"

# Close any file descriptors >= 3. The tests will log file descriptors passed
# to subprocesses. If pytest inherits file descriptors from the process that
# launches it, this perturbs the testing environment. I have seen this with
# processes launched using the VSCode Terminal.

if not os.environ.get("SHELLOUS_CODE_COVERAGE"):
    os.closerange(3, 600)

childwatcher_type = os.environ.get("SHELLOUS_CHILDWATCHER_TYPE")
loop_type = os.environ.get("SHELLOUS_LOOP_TYPE")

if loop_type:
    if sys.platform != "win32" and loop_type == "uvloop":
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
        # Use patched child watcher...
        asyncio.set_child_watcher(PatchedMultiLoopChildWatcher())
    elif childwatcher_type == "default":
        asyncio.set_child_watcher(shellous.DefaultChildWatcher())


@pytest.fixture(autouse=True)
async def report_orphan_tasks():
    "Make sure that all async tests exit with only a single task running."

    # Only run asyncio tests on the main thread. There may be limitations on
    # the childwatcher.
    assert threading.current_thread() is threading.main_thread()

    with _check_open_fds():
        yield
        # Close the childwatcher *before* checking for open fd's.
        if sys.platform != "win32":
            cw = asyncio.get_child_watcher()
            if isinstance(cw, shellous.DefaultChildWatcher):
                cw.close()

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
        await asyncio.sleep(0)
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
    if sys.platform == "win32" or loop_type == "uvloop":
        return set()
    return set(os.listdir("/dev/fd"))


async def _get_children():
    "Return set of child processes. (Not implemented on Windows)"
    if sys.platform == "win32":
        return set()

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


if sys.platform != "win32":

    def _serialize(func):
        """Decorator to serialize a non-reentrant signal function.
        If one client is already in the critical section, set a flag to run the
        section one more time. Testing purposes only.
        """

        lock = threading.Lock()  # Used as atomic test-and-set.
        retry = False

        @functools.wraps(func)
        def _decorator(*args, **kwargs):
            nonlocal retry

            while True:
                if lock.acquire(blocking=False):  # pylint: disable=consider-using-with
                    try:
                        retry = False
                        func(*args, **kwargs)
                    finally:
                        lock.release()
                    if retry:
                        continue
                else:
                    # A signal handler that interrupts an existing handler will
                    # run to completion (LIFO).
                    retry = True
                break

        return _decorator

    class PatchedMultiLoopChildWatcher(asyncio.MultiLoopChildWatcher):
        "Test race condition fixes in MultiLoopChildWatcher."

        def add_child_handler(self, pid, callback, *args):
            loop = asyncio.get_running_loop()
            self._callbacks[pid] = (loop, callback, args)

            # Prevent a race condition in case signal was delivered before
            # callback added.
            signal.raise_signal(signal.SIGCHLD)

        @_serialize
        def _sig_chld(self, signum, frame):
            super()._sig_chld(signum, frame)
