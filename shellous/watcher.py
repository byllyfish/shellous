"Implements DefaultChildWatcher."

import asyncio
import os
import select
import signal
import socket
import sys
import threading

from shellous.log import LOGGER
from shellous.util import close_fds, wait_pid

assert sys.platform != "win32"

# Use a single module level lock for multi-threading.
_LOCK = threading.RLock()


class DefaultChildWatcher(asyncio.AbstractChildWatcher):
    """Uses kqueue/pidfd to monitor for exiting child processes.

    Design Goals:
      1. Independent of any running event loop.
      2. Zero-cost until used.
    """

    def __init__(self):
        self._worker = None

        # kqueue/pidfd do not work if SIGCHLD is set to SIG_IGN in the
        # signal table. On unix systems, the default action for SIGCHLD is to
        # discard the signal; SIG_DFL is compatible and does what we want.
        if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
            raise RuntimeError("SIGCHLD cannot be set to SIG_IGN")

    def add_child_handler(self, pid, callback, *args):
        """Register a new child handler.

        Arrange for callback(pid, returncode, *args) to be called when
        process 'pid' terminates. Specifying another callback for the same
        process replaces the previous handler.

        Note: callback() must be thread-safe.
        """
        assert self._worker, "Must use context manager API"
        self._worker.watch_pid(pid, callback, args)

    def remove_child_handler(self, pid):
        """Removes the handler for process 'pid'.

        The function returns True if the handler was successfully removed,
        False if there was nothing to remove.
        """
        return False  # not supported

    def attach_loop(self, loop):
        """Attach the watcher to an event loop.

        If the watcher was previously attached to an event loop, then it is
        first detached before attaching to the new loop.

        Note: loop may be None.
        """

    def close(self):
        """Close the watcher.

        This must be called to make sure that any underlying resource is freed.
        """
        with _LOCK:
            worker = self._worker
            self._worker = None

        if worker:
            worker.close()

    def is_active(self):
        """Return ``True`` if the watcher is active and is used by the event loop.

        Return True if the watcher is installed and ready to handle process exit
        notifications.
        """
        return True

    def __enter__(self):
        """Enter the watcher's context and allow starting new processes."""
        with _LOCK:
            if not self._worker:
                self._worker = WatcherThread()

        return self

    def __exit__(self, *_args):
        """Exit the watcher's context"""


class WatcherThread(threading.Thread):
    """A Thread that uses kqueue/epoll to monitor for child processes exiting.

    We use a fallback mechanism on macOS because sometimes, for unknown reasons,
    macOS refuses to register a KQ_FILTER_PROC/KQ_NOTE_EXIT event for a known
    child pid even though the child process is *still running*.

    Our fallback strategy relies on polling the "fallback_pids" in a kqueue
    handler that catches SIGCHLD.

    References:
      - https://developer.apple.com/library/archive/technotes/tn2050/_index.html

    """

    def __init__(self):
        "Initialize worker variables and start thread."
        super().__init__(daemon=True)
        if sys.platform == "linux":
            self._agent = EPollAgent()
        else:
            self._agent = KQueueAgent()
        self.start()

    def watch_pid(self, pid, callback, args):
        "Add pid exit callback information to queue and wake up thread."
        LOGGER.debug("watch_pid %r", pid)
        self._agent.register_pid(pid, callback, args)

    def close(self):
        "Tell worker thread to stop."
        self._agent.close()
        self.join()

    def run(self):
        "Override Thread.run()."
        LOGGER.debug("WatcherThread starting %r", self)
        try:
            self._agent.run()
        except BaseException as ex:  # pylint: disable=broad-except
            LOGGER.error("WatcherThread failed %s ex=%r", self, ex, exc_info=True)
            raise
        finally:
            LOGGER.debug("WatcherThread stopping %r", self)


class KQueueAgent:
    "Agent that watches for child exit kqueue event."

    # pylint: disable=no-member

    def __init__(self):
        "Initialize agent variables."
        self._active_pids = {}
        self._kqueue = select.kqueue()

        assert (
            signal.getsignal(signal.SIGCHLD) != signal.SIG_IGN
        ), "kqueue does not work when SIGCHLD set to SIG_IGN"

    def close(self):
        "Tell our thread to exit with a dummy timer event."
        self._add_zero_timer_event()

    def register_pid(self, pid, callback, args):
        "Register a PID with kqueue."
        with _LOCK:
            self._active_pids[pid] = (callback, args)

        if not self._add_proc_event(pid):
            self._pid_not_kqueueable(pid)

    def _pid_not_kqueueable(self, pid):
        "Handle case where an exiting process is no longer kqueue-able."
        with _LOCK:
            callback, args = self._active_pids.pop(pid)

        status = wait_pid(pid)
        if status is not None:
            # Process is finished; issue callback.
            callback(pid, status, *args)
        else:
            # Process is still dying. Spawn a task to poll it.
            asyncio.create_task(_poll_dead_pid(pid, callback, args))

    def run(self):
        "Event loop that handles kqueue events."
        try:
            while True:
                pending = self._kqueue.control(None, 10, 10)
                for event in pending:
                    if event.filter == select.KQ_FILTER_TIMER:
                        # Dummy timer event tells thread to exit.
                        return
                    if event.filter == select.KQ_FILTER_PROC:
                        self._reap_pid(event.ident)

        finally:
            self._kqueue.close()

    def _reap_pid(self, pid):
        """Called when a process exits."""
        with _LOCK:
            callback, args = self._active_pids.pop(pid)

        status = wait_pid(pid)
        if status is not None:
            # Invoke callback function.
            callback(pid, status, *args)
        else:
            # Process is still running.
            LOGGER.critical("_reap_pid: process still running pid=%r", pid)

    def _add_proc_event(self, pid):
        """Add kevent that monitors for process exit.

        Return true if successful.

        kevent can return ESRCH (ProcessLookupError) if it doesn't find the
        process ID. If this happens, return false.
        """
        try:
            event = select.kevent(
                ident=pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            result = self._kqueue.control([event], 0)
            assert result == []
            return True

        except ProcessLookupError:
            # Process already exited, or kqueue random failure.
            return False

        except Exception as ex:  # pylint: disable=broad-except
            LOGGER.error("_add_proc_event: ex=%r", ex)
            raise

    def _add_zero_timer_event(self):
        "Add kevent that expires a timer in 0 seconds."
        try:
            event = select.kevent(
                ident=1,  # Dummy timer event
                filter=select.KQ_FILTER_TIMER,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            )
            result = self._kqueue.control([event], 0)
            assert result == []

        except Exception as ex:  # pylint: disable=broad-except
            LOGGER.error("_add_proc_event: ex=%r", ex)
            raise


class EPollAgent:
    "Agent that watches for child exit epoll event."

    # pylint: disable=no-member

    def __init__(self):
        "Initialize agent variables."
        assert (
            signal.getsignal(signal.SIGCHLD) != signal.SIG_IGN
        ), "pidfd does not work when SIGCHLD set to SIG_IGN"

        self._active_pids = {}  # pid -> (callback, args)
        self._pidfds = {}  # pidfd -> pid
        self._epoll = select.epoll()
        self._selfpipe = _make_selfpipe()
        self._epoll.register(self._selfpipe[1], select.EPOLLIN)


    def register_pid(self, pid, callback, args):
        "Register a PID with epoll."
        with _LOCK:
            self._active_pids[pid] = (callback, args)

        if not self._add_pid_event(pid):
            self._pidfd_failed(pid)

    def _pidfd_failed(self, pid):
        "Handle case where pidfd_open fails."
        with _LOCK:
            callback, args = self._active_pids.pop(pid)

        status = wait_pid(pid)
        if status is not None:
            # Process is finished; issue callback.
            callback(pid, status, *args)
        else:
            # Process is still dying. Spawn a task to poll it.
            asyncio.create_task(_poll_dead_pid(pid, callback, args))

    def close(self):
        "Tell the epoll thread to exit."
        self._selfpipe[0].send(b"\x00")

    def run(self):
        "Event loop that handles epoll events."

        try:
            quit_fd = self._selfpipe[1].fileno()

            while True:
                pending = self._epoll.poll()
                # Each event is a 2-tuple (fd, events):
                for pidfd, events in pending:
                    assert events == select.EPOLLIN
                    if pidfd == quit_fd:
                        return  # all done!
                    self._reap_pidfd(pidfd)

        finally:
            self._epoll.close()
            with _LOCK:
                pidfds = list(self._pidfds.keys())
            close_fds(pidfds)
            close_fds(self._selfpipe)

    def _reap_pidfd(self, pidfd):
        "Handle epoll pidfd event."
        with _LOCK:
            pid = self._pidfds.pop(pidfd)
            callback, args = self._active_pids.pop(pid)

        status = wait_pid(pid)
        if status is not None:
            # Invoke callback function.
            callback(pid, status, *args)
        else:
            # Process is still running.
            LOGGER.critical("_reap_pid: process still running pid=%r", pid)

        self._remove_pidfd_event(pidfd)

    def _add_pid_event(self, pid):
        "Add epoll that monitors for process exit. Return True if successful."
        try:
            pidfd = os.pidfd_open(pid, 0)
        except ProcessLookupError as ex:
            # FIXME: Handle EMFILE and other failure modes.
            LOGGER.error("pidfd_open(%r) failed: ex=%r", pid, ex)
            return False

        with _LOCK:
            self._pidfds[pidfd] = pid
        self._epoll.register(pidfd, select.EPOLLIN)
        return True

    def _remove_pidfd_event(self, pidfd):
        "Remove epoll that monitors for process exit."
        LOGGER.debug("_remove_pidfd_event %r", pidfd)
        self._epoll.unregister(pidfd)
        os.close(pidfd)


async def _poll_dead_pid(pid, callback, args):
    """Poll a pid that we expect to exit and be reap-able very soon.

    This function is called in two cases:
      1. pid is running but no longer kqueueable.
      2. pidfd_open failed because...

    See https://chromium.googlesource.com/chromium/src/base/+/refs/heads/main/process/kill_mac.cc
    """

    for timeout in (0.001, 0.01, 0.1, 1.0, 2.0):
        await asyncio.sleep(timeout)
        status = wait_pid(pid)
        if status is not None:
            callback(pid, status, *args)
            break
    else:
        # Handle case where process is *still* running after 3.111 seconds.
        pass


def _make_selfpipe():
    "Create file descriptor to signal thread exit."
    selfpipe = socket.socketpair()
    for sock in selfpipe:
        sock.setblocking(False)
    return selfpipe
