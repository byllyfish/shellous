"Unit tests that involve daemon threads."

import asyncio
import functools
import os
import sys
import threading
import time

import pytest

from shellous import sh
from shellous.log import log_method

if sys.platform != "win32":
    from shellous import DefaultChildWatcher

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix")


class EventLoopThread(threading.Thread):
    "Thread with its own asyncio event loop."

    def __init__(self):
        super().__init__(daemon=True)
        self._lock = threading.Lock()  # Guards _loop
        self._loop = None
        self._shutdown_fut = None

    @property
    def loop(self):
        result = None
        for _ in range(3):
            with self._lock:
                result = self._loop
            if result:
                break
            time.sleep(0.1)
        return result

    def future(self, coro):
        "Run coroutine in separate thread. Returns concurrent.Future."
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self):
        "Override Thread.run()."
        asyncio.run(self._wait())

    def stop(self):
        "Called from another thread to stop this one."
        self.loop.call_soon_threadsafe(self._stop)
        self.join()

    @log_method(True)
    async def _wait(self):
        "Wait for shutdown future to be triggered."
        loop = asyncio.get_running_loop()
        self._shutdown_fut = loop.create_future()

        with self._lock:
            self._loop = loop

        # FIXME: asyncio.run doesn't provide a nice way to call attach_loop on a
        # PidfdChildWatcher. We call it here using the running loop. I'm not
        # sure what happens if you run the same PidfdChildWatcher in
        # multiple threads though...
        cw = asyncio.get_child_watcher()
        if cw.__class__.__name__ in ("PidfdChildWatcher"):
            cw.attach_loop(loop)

        await self._shutdown_fut

    def _stop(self):
        "Trigger shutdown future."
        if not self._shutdown_fut.done():
            self._shutdown_fut.set_result(1)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_args):
        self.stop()


def run_in_thread(child_watcher_name="ThreadedChildWatcher"):
    "Decorator to run async function in a daemon thread with private event loop."

    def _decorator(coro):
        @functools.wraps(coro)
        def _wrap(*args, **kwargs):
            if child_watcher_name == "DefaultChildWatcher":
                child_watcher = DefaultChildWatcher()
            else:
                child_watcher = getattr(asyncio, child_watcher_name)()
            asyncio.set_child_watcher(child_watcher)

            if child_watcher_name in ("FastChildWatcher", "SafeChildWatcher"):
                # SafeChildWatcher and FastChildWatcher require a viable
                # asyncio event loop in the main thread, so give them one...

                def blocking_call():
                    with EventLoopThread() as thread:
                        fut = thread.future(coro(*args, **kwargs))
                        fut.result()

                async def _main():
                    await asyncio.to_thread(blocking_call)

                asyncio.run(_main())

            else:
                # Run every other child watcher using a daemon thread while the
                # main thread waits in concurrent.Future.

                with EventLoopThread() as thread:
                    fut = thread.future(coro(*args, **kwargs))
                    fut.result()

        return _wrap

    return _decorator


_CHILD_WATCHER_MAP = {
    "fast": "FastChildWatcher",
    "safe": "SafeChildWatcher",
    "pidfd": "PidfdChildWatcher",
    "default": "DefaultChildWatcher",
}

_CW_TYPE = os.environ.get("SHELLOUS_CHILDWATCHER_TYPE", "<unset>")

CHILD_WATCHER = _CHILD_WATCHER_MAP.get(_CW_TYPE, "ThreadedChildWatcher")
XFAIL_CHILDWATCHER = CHILD_WATCHER in (
    "SafeChildWatcher",
    "FastChildWatcher",
)


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_echo():
    "Test echo in another thread."
    result = await sh("echo", "abc")
    assert result == "abc\n"


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_pipe():
    "Test pipe in another thread."
    result = await (sh("echo", "abc") | sh("cat"))
    assert result == "abc\n"


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_procsub():
    "Test process substituion in another thread."
    result = await sh("cat", sh("echo", "abc"), sh("echo", "def"))
    assert result == "abc\ndef\n"


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_pty():
    "Test pty in another thread."
    if sys.platform == "linux":
        ls = sh("ls", "--color=never")
    else:
        ls = sh("ls")

    result = await ls("README.md").set(pty=True)
    assert result == "README.md\r\n"


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_pipe_long():
    "Test pipe in another thread."
    # Create a pipe with 1 echo, and 9 cat commands.
    pipe = sh("echo", "xyz")
    for _ in range(9):
        pipe |= sh("cat")

    result = await pipe
    assert result == "xyz\n"
