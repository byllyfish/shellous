"Unit tests that involve daemon threads."

import asyncio
import functools
import os
import sys
import threading
import time

import pytest
import shellous

unix_only = pytest.mark.skipif(sys.platform == "win32", reason="Unix")
pytestmark = [pytest.mark.asyncio, unix_only]


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

    async def _wait(self):
        "Wait for shutdown future to be triggered."
        loop = asyncio.get_running_loop()
        self._shutdown_fut = loop.create_future()

        with self._lock:
            self._loop = loop

        # asyncio.run doesn't provide a nice way to call attach_loop on a
        # PidfdChildWatcher? (FIXME)
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
            child_watcher = getattr(asyncio, child_watcher_name)()
            asyncio.set_child_watcher(child_watcher)

            # MultiLoopChildWatcher: call attach_loop to prepare for action.
            # `attach_loop` must be called from MainThread.
            if child_watcher_name == "MultiLoopChildWatcher":
                child_watcher.attach_loop(None)

            if child_watcher_name not in ("FastChildWatcher", "SafeChildWatcher"):
                # Run every other child watcher using a daemon thread while the
                # main thread is blocked in concurrent.Future.
                with EventLoopThread() as thread:
                    fut = thread.future(coro(*args, **kwargs))
                    fut.result()
            else:
                # SafeChildWatcher and FastChildWatcher require a viable
                # asyncio event loop in the main thread, so give them one...

                def blocking_call():
                    with EventLoopThread() as thread:
                        fut = thread.future(coro(*args, **kwargs))
                        fut.result()

                async def _main():
                    await asyncio.to_thread(blocking_call)

                asyncio.run(_main())

        return _wrap

    return _decorator


_CHILD_WATCHER_MAP = {
    "fast": "FastChildWatcher",
    "safe": "SafeChildWatcher",
    "pidfd": "PidfdChildWatcher",
    "multi": "MultiLoopChildWatcher",
}

_CW_TYPE = os.environ.get("SHELLOUS_CHILDWATCHER_TYPE")

CHILD_WATCHER = _CHILD_WATCHER_MAP.get(_CW_TYPE, "ThreadedChildWatcher")
XFAIL_CHILDWATCHER = CHILD_WATCHER in ("SafeChildWatcher", "FastChildWatcher")


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_echo():
    "Test echo in another thread."
    sh = shellous.context()
    result = await sh("echo", "abc")
    assert result == "abc\n"


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_pipe():
    "Test pipe in another thread."
    sh = shellous.context()
    result = await (sh("echo", "abc") | sh("cat"))
    assert result == "abc\n"


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_procsub():
    "Test process substituion in another thread."
    sh = shellous.context()
    result = await sh("cat", sh("echo", "abc"), sh("echo", "def"))
    assert result == "abc\ndef\n"


@pytest.mark.xfail(XFAIL_CHILDWATCHER, reason="xfail_childwatcher")
@run_in_thread(CHILD_WATCHER)
async def test_thread_pty():
    "Test pty in another thread."
    sh = shellous.context()
    result = await sh("ls", "README.md").set(pty=True)
    assert result == "README.md\r\n"
