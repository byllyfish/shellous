"Configure common fixtures for pytest."

import asyncio
import contextlib
import functools
import gc
import os
import re
import signal
import sys
import threading

import pytest
import shellous

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
    gc.collect()  # Force gc for pypy.
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


# ===============================================================================

"""
The tests will not pass on Python 3.8 because b.p.o 40607 is not backported.

Monkey patch asyncio.wait_for for testing purposes.

Copied from: https://github.com/python/cpython/blob/main/Lib/asyncio/tasks.py
"""

import functools
from asyncio import ensure_future, events, exceptions


def _release_waiter(waiter, *args):
    if not waiter.done():
        waiter.set_result(None)


async def wait_for(fut, timeout):
    """Wait for the single Future or coroutine to complete, with timeout.
    Coroutine will be wrapped in Task.
    Returns result of the Future or coroutine.  When a timeout occurs,
    it cancels the task and raises TimeoutError.  To avoid the task
    cancellation, wrap it in shield().
    If the wait is cancelled, the task is also cancelled.
    This function is a coroutine.
    """
    loop = events.get_running_loop()

    if timeout is None:
        return await fut

    if timeout <= 0:
        fut = ensure_future(fut, loop=loop)

        if fut.done():
            return fut.result()

        await _cancel_and_wait(fut, loop=loop)
        try:
            fut.result()
        except exceptions.CancelledError as exc:
            raise exceptions.TimeoutError() from exc
        else:
            raise exceptions.TimeoutError()

    waiter = loop.create_future()
    timeout_handle = loop.call_later(timeout, _release_waiter, waiter)
    cb = functools.partial(_release_waiter, waiter)

    fut = ensure_future(fut, loop=loop)
    fut.add_done_callback(cb)

    try:
        # wait until the future completes or the timeout
        try:
            await waiter
        except exceptions.CancelledError:
            if fut.done():
                return fut.result()
            else:
                fut.remove_done_callback(cb)
                # We must ensure that the task is not running
                # after wait_for() returns.
                # See https://bugs.python.org/issue32751
                await _cancel_and_wait(fut, loop=loop)
                raise

        if fut.done():
            return fut.result()
        else:
            fut.remove_done_callback(cb)
            # We must ensure that the task is not running
            # after wait_for() returns.
            # See https://bugs.python.org/issue32751
            await _cancel_and_wait(fut, loop=loop)
            # In case task cancellation failed with some
            # exception, we should re-raise it
            # See https://bugs.python.org/issue40607
            try:
                fut.result()
            except exceptions.CancelledError as exc:
                raise exceptions.TimeoutError() from exc
            else:
                raise exceptions.TimeoutError()
    finally:
        timeout_handle.cancel()


async def _cancel_and_wait(fut, loop):
    """Cancel the *fut* future or task and wait until it completes."""

    waiter = loop.create_future()
    cb = functools.partial(_release_waiter, waiter)
    fut.add_done_callback(cb)

    try:
        fut.cancel()
        # We cannot wait on *fut* directly to make
        # sure _cancel_and_wait itself is reliably cancellable.
        await waiter
    finally:
        fut.remove_done_callback(cb)


# MONKEY PATCH here...
asyncio.wait_for = wait_for  # type: ignore
