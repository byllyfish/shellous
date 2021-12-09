"""Implements DefaultChildWatcher.

Design Goals:
    1. Independent of any running event loop.
    2. Zero-cost until used.

References:
    - https://developer.apple.com/library/archive/technotes/tn2050/_index.html
    - https://chromium.googlesource.com/chromium/src/base/+/refs/heads/main/process/kill_mac.cc

"""

import asyncio
import os
import select
import signal
import sys
import threading

from shellous.log import LOGGER, log_thread
from shellous.util import close_fds, wait_pid

assert sys.platform != "win32"


def _check_sigchld():
    """Check that SIGCHLD is not set to SIG_IGN.

    kqueue/pidfd do not work if SIGCHLD is set to SIG_IGN in the
    signal table. On unix systems, the default action for SIGCHLD is to
    discard the signal so SIG_DFL is fine.
    """
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        raise RuntimeError("SIGCHLD cannot be set to SIG_IGN")


class DefaultChildWatcher(asyncio.AbstractChildWatcher):
    "Use platform-dependent mechanism to monitor for exiting child processes."

    def __init__(self):
        "Initialize child watcher."
        self._agent = None
        self._lock = threading.Lock()

    def add_child_handler(self, pid, callback, *args):
        """Register a new child handler.

        Arrange for callback(pid, returncode, *args) to be called when
        process 'pid' terminates. Specifying another callback for the same
        process replaces the previous handler.

        Note: callback() must be thread-safe.
        """
        if not self._agent:  # ATOMIC: self._agent
            with self._lock:
                self._init_agent()

        self._agent.watch_pid(pid, callback, args)  # ATOMIC: self._agent

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
        # no op

    def close(self):
        """Close the watcher.

        This must be called to make sure that any underlying resource is freed.
        """
        with self._lock:
            agent = self._agent
            self._agent = None

        if agent:
            agent.close()

    def is_active(self):
        """Return ``True`` if the watcher is active and is used by the event loop.

        Return True if the watcher is installed and ready to handle process exit
        notifications.
        """
        return True

    def __enter__(self):
        """Enter the watcher's context and allow starting new processes."""
        return self

    def __exit__(self, *_args):
        """Exit the watcher's context"""
        return None

    def _init_agent(self):
        "Construct child watcher agent."
        if self._agent:
            return

        if hasattr(os, "pidfd_open"):
            agent_class = EPollAgent
        elif hasattr(select, "kqueue"):
            agent_class = KQueueAgent
        else:
            agent_class = ThreadAgent

        self._agent = agent_class()


class KQueueAgent:
    "Agent that watches for child exit kqueue event."

    # pylint: disable=no-member

    def __init__(self):
        "Initialize agent variables."
        _check_sigchld()
        self._pids = {}  # pid -> (callback, args)
        self._kqueue = select.kqueue()
        self._thread = threading.Thread(
            target=self._run,
            name="KQueueAgent.run",
            daemon=True,
        )
        self._thread.start()

    def close(self):
        "Tell our thread to exit with a dummy timer event."
        self._add_kevent(
            1,
            select.KQ_FILTER_TIMER,
            select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
        )
        self._thread.join()

    def watch_pid(self, pid, callback, args):
        "Register a PID with kqueue."
        self._pids[pid] = (callback, args)  # ATOMIC: self._pids[] = ...

        try:
            self._add_kevent(
                pid,
                select.KQ_FILTER_PROC,
                select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                select.KQ_NOTE_EXIT,
            )
            LOGGER.debug("_add_kevent pid=%r", pid)
        except ProcessLookupError:
            self._kevent_failed(pid)

    def _kevent_failed(self, pid):
        "Handle case where an exiting process is no longer kqueue-able."
        callback, args = self._pids.pop(pid)  # ATOMIC: self._pids.pop()

        LOGGER.debug("_kevent_failed pid=%r", pid)

        status = wait_pid(pid)
        if status is not None:
            _invoke_callback(callback, pid, status, args)
        else:
            # Process is still dying. Spawn a task to poll it.
            asyncio.create_task(_poll_dead_pid(pid, callback, args))

    @log_thread(True)
    def _run(self):
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
        """Called by event loop when a process exits."""
        callback, args = self._pids.pop(pid)  # ATOMIC: self._pids.pop()

        LOGGER.debug("_reap_pid pid=%r", pid)

        status = wait_pid(pid)
        if status is not None:
            _invoke_callback(callback, pid, status, args)
        else:
            LOGGER.critical("_reap_pid: process still running pid=%r", pid)

    def _add_kevent(self, ident, kfilter, flags, fflags=0):
        "Add specified kevent to kqueue."
        event = select.kevent(ident, kfilter, flags, fflags)
        self._kqueue.control([event], 0)


class EPollAgent:
    "Agent that watches for child exit epoll event."

    # pylint: disable=no-member

    def __init__(self):
        "Initialize agent variables."
        _check_sigchld()
        self._pidfds = {}  # pidfd -> (pid, callback, args)
        self._epoll = select.epoll()
        self._selfpipe = os.pipe()
        self._epoll.register(self._selfpipe[0], select.EPOLLIN)
        self._thread = threading.Thread(
            target=self._run,
            name="EPollAgent.run",
            daemon=True,
        )
        self._thread.start()

    def close(self):
        "Tell the epoll thread to exit."
        if self._selfpipe is not None:
            os.write(self._selfpipe[1], b"\x00")
        self._thread.join()

    def watch_pid(self, pid, callback, args):
        "Register a PID with epoll."
        try:
            self._add_pidfd(pid, callback, args)
        except ProcessLookupError:
            self._pidfd_process_missing(pid, callback, args)
        except Exception as ex:
            LOGGER.warning("EPollAgent.watch_pid failed pid=%r ex=%r", pid, ex)
            self._pidfd_error(pid, callback, args)
            raise

    @staticmethod
    def _pidfd_process_missing(pid, callback, args):
        "Handle case where pidfd_open fails with a ProcessLookupError (ESRCH)."
        LOGGER.debug("_pidfd_process_missing pid=%r", pid)

        status = wait_pid(pid)
        if status is not None:
            _invoke_callback(callback, pid, status, args)
        else:
            # Process is still dying. Spawn a task to poll it.
            asyncio.create_task(_poll_dead_pid(pid, callback, args))

    @staticmethod
    def _pidfd_error(pid, callback, args):
        """Handle case where pidfd_open fails with any error.

        This can happen if the process runs out of file descriptors (EMFILE).
        """
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        asyncio.create_task(_poll_dead_pid(pid, callback, args))

    @log_thread(True)
    def _run(self):
        "Event loop that handles epoll events."
        try:
            pipe_fd = self._selfpipe[0]

            while True:
                pending = self._epoll.poll()
                for pidfd, _events in pending:
                    if pidfd == pipe_fd:
                        return  # all done!
                    self._reap_pidfd(pidfd)
                    self._remove_pidfd(pidfd)

        finally:
            self._epoll.close()
            pidfds = list(self._pidfds.keys())  # ATOMIC: self._pidfds.keys()
            close_fds(pidfds + list(self._selfpipe))
            self._selfpipe = None

    def _reap_pidfd(self, pidfd):
        "Handle epoll pidfd event."
        pid, callback, args = self._pidfds.pop(pidfd)  # ATOMIC: self._pidfds.pop()

        LOGGER.debug("_reap_pidfd pidfd=%r pid=%r", pidfd, pid)

        status = wait_pid(pid)
        if status is not None:
            _invoke_callback(callback, pid, status, args)
        else:
            LOGGER.critical("_reap_pid: process still running pid=%r", pid)

    def _add_pidfd(self, pid, callback, args):
        "Add epoll that monitors for process exit."
        pidfd = os.pidfd_open(pid, 0)
        self._pidfds[pidfd] = (pid, callback, args)  # ATOMIC: self._pidfds[]

        self._epoll.register(pidfd, select.EPOLLIN)
        LOGGER.debug("_add_pidfd registered pidfd=%r pid=%r", pidfd, pid)

    def _remove_pidfd(self, pidfd):
        "Remove epoll that monitors for process exit."
        self._epoll.unregister(pidfd)
        os.close(pidfd)


class ThreadAgent:
    "Agent that uses threads to watch for child exits."

    def __init__(self):
        "Initialize agent."
        self._pids = {}  # pid -> Thread

    def close(self):
        """There is no way to force-close the threads. Log a message if there
        are still any live threads."""
        threads = list(self._pids.values())
        live_threads = [thread.name for thread in threads if thread.is_alive()]
        if live_threads:
            LOGGER.warning("ThreadAgent: Child processes exist: %r", live_threads)

    def watch_pid(self, pid, callback, args):
        "Start a thread to watch the PID."
        thread = threading.Thread(
            target=self._reap_pid,
            args=(pid, callback, args),
            name=f"ThreadAgent.reap_pid-{pid}",
            daemon=True,
        )
        self._pids[pid] = thread
        thread.start()

    @log_thread(True)
    def _reap_pid(self, pid, callback, args):
        "Call waitpid synchronously."
        status = wait_pid(pid, block=True)
        if status is not None:
            _invoke_callback(callback, pid, status, args)
        else:
            LOGGER.critical("_reap_pid: process still running pid=%r", pid)

        self._pids.pop(pid)


async def _poll_dead_pid(pid, callback, args):
    """Poll a pid that we expect to exit and be reap-able very soon."""
    for timeout in (0.001, 0.01, 0.1, 1.0, 2.0):
        await asyncio.sleep(timeout)
        status = wait_pid(pid)
        if status is not None:
            _invoke_callback(callback, pid, status, args)
            break
    else:
        # Handle case where process is *still* running after 3.111 seconds.
        LOGGER.critical("Pid %r is not exiting after several seconds.", pid)


def _invoke_callback(callback, pid, status, args):
    """Invoke callback function:  callback(pid, status, *args)

    ```
        # The code we are calling looks like this (self is the event loop.)
        # https://github.com/.../asyncio/unix_events.py#L225
        def _child_watcher_callback(self, pid, returncode, transp):
            self.call_soon_threadsafe(transp._process_exited, returncode)
    ```

    call_soon_threadsafe raises a RuntimeError if the loop is already closed.
    """
    try:
        callback(pid, status, *args)
    except RuntimeError as ex:
        LOGGER.warning(
            "DefaultChildWatcher callback pid=%r status=%r ex=%r",
            pid,
            status,
            ex,
        )
