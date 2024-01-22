"Implements the Prompt utility class."

import asyncio
import codecs
import io
import re
from typing import Optional, Union

from shellous import pty_util
from shellous.harvest import harvest_results
from shellous.log import LOG_PROMPT, LOGGER
from shellous.result import Result
from shellous.runner import PipeRunner, Runner
from shellous.util import encode_bytes

_DEFAULT_LINE_END = "\n"
_DEFAULT_CHUNK_SIZE = 16384

_LOG_LIMIT = 2000
_LOG_LIMIT_END = 30


class Prompt:
    """Utility class to help with an interactive prompt session.

    When you are controlling a co-process you will usually "send" some
    text to it, and then "expect" a response. The "expect" operation can use
    a regular expression to match different types of responses.

    Create a new `Prompt` instance using the `prompt()` API.

    ```
    # In this example, we are using a default prompt of "??? ".
    # Setting the PS1 environment variable tells the shell to use this as
    # the shell prompt.
    cmd = sh("sh").env(PS1="??? ").set(pty=True)

    async with cmd.prompt("??? ", timeout=3.0) as cli:
        # Turn off terminal echo.
        cli.echo = False

        # Wait for greeting and initial prompt. Calling expect() with no
        # argument will match the default prompt "??? ".
        greeting, _ = await cli.expect()

        # Send a command and wait for the response.
        await cli.send("echo hello")
        answer, _ = await cli.expect()
        assert answer == "hello\\r\\n"
    ```
    """

    _runner: Union[Runner, PipeRunner]
    _encoding: str
    _default_prompt: Optional[re.Pattern[str]]
    _default_timeout: Optional[float]
    _normalize_newlines: bool
    _chunk_size: int
    _decoder: codecs.IncrementalDecoder
    _pending: str = ""
    _at_eof: bool = False
    _result: Optional[Result] = None

    def __init__(
        self,
        runner: Runner,
        *,
        default_prompt: Union[str, list[str], re.Pattern[str], None] = None,
        default_timeout: Optional[float] = None,
        normalize_newlines: bool = False,
        _chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ):
        assert runner.stdin is not None
        assert runner.stdout is not None

        if isinstance(default_prompt, (str, list)):
            default_prompt = _regex_compile_exact(default_prompt)

        self._runner = runner
        self._encoding = runner.options.encoding
        self._default_prompt = default_prompt
        self._default_timeout = default_timeout
        self._normalize_newlines = normalize_newlines
        self._chunk_size = _chunk_size
        self._decoder = _make_decoder(self._encoding, normalize_newlines)

        if LOG_PROMPT:
            LOGGER.info(
                "Prompt[pid=%s]: --- BEGIN --- name=%r",
                self._runner.pid,
                self._runner.name,
            )

    @property
    def at_eof(self) -> bool:
        "True if the prompt reader is at the end of file."
        return self._at_eof and not self._pending

    @property
    def pending(self) -> str:
        """Characters that still remain in the `pending` buffer."""
        return self._pending

    @property
    def echo(self) -> bool:
        """True if PTY is in echo mode.

        If the runner is not using a PTY, always return False.

        When the process is using a PTY, you can enable/disable terminal echo
        mode by setting the `echo` property to True/False.
        """
        if not isinstance(self._runner, Runner) or self._runner.pty_fd is None:
            return False
        return pty_util.get_term_echo(self._runner.pty_fd)

    @echo.setter
    def echo(self, value: bool) -> None:
        """Set echo mode for the PTY.

        Raise an error if the runner is not using a PTY.
        """
        if not isinstance(self._runner, Runner) or self._runner.pty_fd is None:
            raise RuntimeError("Cannot set echo mode. Not running in a PTY.")
        pty_util.set_term_echo(self._runner.pty_fd, value)

    @property
    def result(self) -> Result:
        """The `Result` of the co-process when it exited.

        You can only retrieve this property *after* the `async with` block
        exits where the co-process is running:

        ```
        async with cmd.prompt() as cli:
            ...
        # Access `cli.result` here.
        ```

        Inside the `async with` block, raise a RuntimeError because the process
        has not exited yet.
        """
        if self._result is None:
            raise RuntimeError("Prompt process is still running")
        return self._result

    async def send(
        self,
        text: Union[bytes, str],
        *,
        end: Optional[str] = _DEFAULT_LINE_END,
        no_echo: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Write some `text` to co-process standard input and append a newline.

        The `text` parameter is the string that you want to write. Use a `bytes`
        object to send some raw bytes (e.g. to the terminal driver).

        The default line ending is "\n". Use the `end` parameter to change the
        line ending. To omit the line ending entirely, specify `end=None`.

        Set `no_echo` to True when you are writing a password. When you set
        `no_echo` to True, the `send` method will wait for terminal
        echo mode to be disabled before writing the text. If shellous logging
        is enabled, the sensitive information will **not** be logged.

        Use the `timeout` parameter to override the default timeout. Normally,
        data is delivered immediately and this method returns making a trip
        through the event loop. However, there are situations where the
        co-process input pipeline fills and we have to wait for it to
        drain.

        When this method needs to wait for the co-process input pipe to drain,
        this method will concurrently read from the output pipe into the pending
        buffer. This is necessary to avoid a deadlock situation where everything
        stops because neither process can make progress.
        """
        if end is None:
            end = ""

        if no_echo:
            await self._wait_no_echo()

        if isinstance(text, bytes):
            data = text + encode_bytes(end, self._encoding)
        else:
            data = encode_bytes(text + end, self._encoding)

        stdin = self._runner.stdin
        assert stdin is not None
        stdin.write(data)

        if LOG_PROMPT:
            self._log_send(data, no_echo)

        # Drain our write to stdin.
        cancelled, ex = await harvest_results(
            self._drain(stdin),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()
        if isinstance(ex[0], Exception):
            raise ex[0]

    async def expect(
        self,
        prompt: Union[str, list[str], re.Pattern[str], None] = None,
        *,
        timeout: Optional[float] = None,
    ) -> tuple[str, re.Match[str]]:
        """Read from co-process standard output until `prompt` pattern matches.

        Returns a 2-tuple of (output, match) where `output` is the text *before*
        the prompt pattern and `match` is a `re.Match` object for the prompt
        text itself.

        If `expect` reaches EOF or a timeout occurs before the prompt pattern
        matches, it raises an `EOFError` or `asyncio.TimeoutError`. The unread
        characters will be available in the `pending` buffer.

        After this method returns, there may still be characters read from stdout
        that remain in the `pending` buffer. These are the characters *after* the
        prompt pattern. You can examine these using the `pending` property.
        Subsequent calls to expect will examine this buffer first before
        reading new data from the output pipe.

        By default, this method will use the default `timeout` for the `Prompt`
        object if one is set. You can use the `timeout` parameter to specify
        a custom timeout in seconds.

        Prompt Patterns
        ~~~~~~~~~~~~~~~

        The `expect()` method supports matching fixed strings and regular
        expressions. The type of the `prompt` parameter determines the type of
        search.

        No argument or `None`:
            Use the default prompt pattern. If there is no default prompt,
            raise a TypeError.
        `str`:
            Match this string exactly.
        `list[str]`:
            Match one of these strings exactly.
        `re.Pattern[str]`:
            Match the given regular expression.

        When matching a regular expression, only a single Pattern object is
        supported. To match multiple regular expressions, combine them into a
        single regular expression using *alternation* syntax (|).

        The `expect()` method returns a 2-tuple (output, match). The `match`
        is the result of the regular expression search (re.Match). If you
        specify your prompt as a string or list of strings, it is still compiled
        into a regular expression that produces an `re.Match` object. You can
        examine the `match` object to determine the prompt value found.

        This method conducts a regular expression search on streaming data. The
        `expect()` method reads a new chunk of data into the `pending` buffer
        and then searches it. You must be careful in writing a regular
        expression so that the search is agnostic to how the incoming chunks of
        data arrive. Consider including a boundary condition at the end of your pattern.
        For example, instead of searching for the open-ended pattern`[a-z]+`,
        search for the pattern `[a-z]+[^a-z]` which ends with a non-letter
        character.

        Examples
        ~~~~~~~~

        Expect an exact string:

        ```
        await cli.expect("ftp> ")
        await cli.send(command)
        response, _ = await cli.expect("ftp> ")
        ```

        Expect a choice of strings:

        ```
        _, m = await cli.expect(["Login: ", "Password: ", "ftp> "])
        match m[0]:
            case "Login: ":
                await cli.send(login)
            case "Password: ":
                await cli.send(password)
            case "ftp> ":
                await cli.send(command)
        ```

        Read until EOF:

        ```
        data = await cli.read_all()
        ```

        Read the contents of the `pending` buffer without filling the buffer
        with any new data from the co-process pipe:

        ```
        data = await cli.read_pending()
        ```
        """
        if prompt is None:
            prompt = self._default_prompt
            if prompt is None:
                raise TypeError("prompt is required when default prompt is not set")
        elif isinstance(prompt, (str, list)):
            prompt = _regex_compile_exact(prompt)

        if self._pending:
            result = self._search_pending(prompt)
            if result is not None:
                return result

        if self._at_eof:
            raise EOFError("Prompt has reached EOF")

        cancelled, (result,) = await harvest_results(
            self._read_to_pattern(prompt),
            timeout=timeout or self._default_timeout,
        )
        if cancelled:
            raise asyncio.CancelledError()
        if isinstance(result, Exception):
            raise result

        return result

    async def read_all(
        self,
        *,
        timeout: Optional[float] = None,
    ) -> str:
        """Read from co-process output until EOF.

        If we are already at EOF, return "".
        """
        if not self._at_eof:
            cancelled, (result,) = await harvest_results(
                self._read_some(tag="@read_all"),
                timeout=timeout or self._default_timeout,
            )
            if cancelled:
                raise asyncio.CancelledError()
            if isinstance(result, Exception):
                raise result

        return self.read_pending()

    def read_pending(self) -> str:
        """Read the contents of the pending buffer and empty it.

        This method does not fill the pending buffer with any new data from the
        co-process output pipe. If the pending buffer is already empty, return
        "".
        """
        result = self._pending
        self._pending = ""
        return result

    async def command(
        self,
        text: str,
        *,
        end: str = _DEFAULT_LINE_END,
        no_echo: bool = False,
        prompt: Union[str, re.Pattern[str], None] = None,
        timeout: Optional[float] = None,
        allow_eof: bool = False,
    ) -> str:
        """Send some text to the co-process and return the response.

        This method is equivalent to calling send() following by expect().
        However, the return value is simpler; `command()` does not return the
        `re.Match` object.

        If you call this method *after* the co-process output pipe has already
        returned EOF, raise `EOFError`.

        If `allow_eof` is True, this method will read data up to EOF instead of
        raising an EOFError.
        """
        if self._at_eof:
            raise EOFError("Prompt has reached EOF")

        await self.send(text, end=end, no_echo=no_echo, timeout=timeout)
        try:
            result, _ = await self.expect(prompt, timeout=timeout)
        except EOFError:
            if not allow_eof:
                raise
            result = self.read_pending()

        return result

    def close(self) -> None:
        "Close stdin to end the prompt session."
        stdin = self._runner.stdin
        assert stdin is not None

        if isinstance(self._runner, Runner) and self._runner.pty_eof:
            # Write EOF twice; once to end the current line, and the second
            # time to signal the end.
            stdin.write(self._runner.pty_eof * 2)
            if LOG_PROMPT:
                LOGGER.info("Prompt[pid=%s] send: [[EOF]]", self._runner.pid)

        else:
            stdin.close()
            if LOG_PROMPT:
                LOGGER.info("Prompt[pid=%s] close", self._runner.pid)

    def _finish_(self) -> None:
        "Internal method called when process exits to fetch the `Result` and cache it."
        self._result = self._runner.result(check=False)
        if LOG_PROMPT:
            LOGGER.info(
                "Prompt[pid=%s]: --- END --- result=%r",
                self._runner.pid,
                self._result,
            )

    async def _read_to_pattern(
        self,
        pattern: re.Pattern[str],
    ) -> tuple[str, re.Match[str]]:
        """Read text up to part that matches the pattern.

        Returns 2-tuple with (text, match).
        """
        stdout = self._runner.stdout
        assert stdout is not None
        assert self._chunk_size > 0

        while not self._at_eof:
            _prev_len = len(self._pending)  # debug check

            try:
                # Read chunk and check for EOF.
                chunk = await stdout.read(self._chunk_size)
                if not chunk:
                    self._at_eof = True
            except asyncio.CancelledError:
                if LOG_PROMPT:
                    LOGGER.info(
                        "Prompt[pid=%s] receive cancelled: pending=%r",
                        self._runner.pid,
                        self._pending,
                    )
                raise

            if LOG_PROMPT:
                self._log_receive(chunk)

            # Decode eligible bytes into our buffer.
            data = self._decoder.decode(chunk, final=self._at_eof)
            if not data and not self._at_eof:
                continue
            self._pending += data

            result = self._search_pending(pattern)
            if result is not None:
                return result

            assert self._at_eof or len(self._pending) > _prev_len  # debug check

        raise EOFError("Prompt has reached EOF")

    def _search_pending(
        self,
        pattern: re.Pattern[str],
    ) -> Optional[tuple[str, re.Match[str]]]:
        """Search our `pending` buffer for the pattern.

        If we find a match, we return the data up to the portion that matched
        and leave the trailing data in the `pending` buffer. This method returns
        (result, match) or None if there is no match.
        """
        found = pattern.search(self._pending)
        if found:
            result = self._pending[0 : found.start(0)]
            self._pending = self._pending[found.end(0) :]
            if LOG_PROMPT:
                LOGGER.info(
                    "Prompt[pid=%s] found: %r [%s CHARS PENDING]",
                    self._runner.pid,
                    found,
                    len(self._pending),
                )
            return (result, found)

        return None

    async def _drain(self, stream: asyncio.StreamWriter) -> None:
        "Drain stream while reading into buffer concurrently."
        read_task = asyncio.create_task(
            self._read_some(tag="@drain", concurrent_cancel=True)
        )
        try:
            await stream.drain()

        finally:
            if not read_task.done():
                read_task.cancel()
                await read_task

    async def _read_some(
        self,
        *,
        tag: str = "",
        concurrent_cancel: bool = False,
    ) -> None:
        "Read into `pending` buffer until cancelled or EOF."
        stdout = self._runner.stdout
        assert stdout is not None
        assert self._chunk_size > 0

        while not self._at_eof:
            # Yield time to other tasks; read() doesn't yield as long as there
            # is data to read. We need to provide a cancel point when this
            # method is called during `drain`.
            if concurrent_cancel:
                await asyncio.sleep(0)

            # Read chunk and check for EOF.
            chunk = await stdout.read(self._chunk_size)
            if not chunk:
                self._at_eof = True

            if LOG_PROMPT:
                self._log_receive(chunk, tag)

            # Decode eligible bytes into our buffer.
            data = self._decoder.decode(chunk, final=self._at_eof)
            self._pending += data

    async def _wait_no_echo(self):
        "Wait for terminal echo mode to be disabled."
        if LOG_PROMPT:
            LOGGER.info("Prompt[pid=%s] wait: no_echo", self._runner.pid)

        for _ in range(4 * 30):
            if not self.echo:
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("Timed out: Terminal echo mode remains enabled.")

    def _log_send(self, data: bytes, no_echo: bool):
        "Log data as it is being sent."
        pid = self._runner.pid

        if no_echo:
            LOGGER.info("Prompt[pid=%s] send: [[HIDDEN]]", pid)
        else:
            data_len = len(data)
            if data_len > _LOG_LIMIT:
                LOGGER.info(
                    "Prompt[pid=%s] send: [%d B] %r...%r",
                    pid,
                    data_len,
                    data[: _LOG_LIMIT - _LOG_LIMIT_END],
                    data[-_LOG_LIMIT_END:],
                )
            else:
                LOGGER.info(
                    "Prompt[pid=%s] send: [%d B] %r",
                    pid,
                    data_len,
                    data,
                )

    def _log_receive(self, data: bytes, tag: str = ""):
        "Log data as it is being received."
        pid = self._runner.pid
        data_len = len(data)

        if data_len > _LOG_LIMIT:
            LOGGER.info(
                "Prompt[pid=%s] receive%s: [%d B] %r...%r",
                pid,
                tag,
                data_len,
                data[: _LOG_LIMIT - _LOG_LIMIT_END],
                data[-_LOG_LIMIT_END:],
            )
        else:
            LOGGER.info(
                "Prompt[pid=%s] receive%s: [%d B] %r",
                pid,
                tag,
                data_len,
                data,
            )


def _make_decoder(encoding: str, normalize_newlines: bool) -> codecs.IncrementalDecoder:
    "Construct an incremental decoder for the specified encoding settings."
    enc = encoding.split(maxsplit=1)
    decoder_class = codecs.getincrementaldecoder(enc[0])
    decoder = decoder_class(*enc[1:])
    if normalize_newlines:
        decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
    return decoder


def _regex_compile_exact(pattern: Union[str, list[str]]) -> re.Pattern[str]:
    "Compile a regex that matches an exact string or set of strings."
    if isinstance(pattern, list):
        return re.compile("|".join(re.escape(s) for s in pattern))
    return re.compile(re.escape(pattern))
