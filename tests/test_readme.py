"Test the asyncio REPL calls in the README (MacOS only)."

import asyncio
import os
import re
import sys

import pytest
from shellous import sh
from shellous.harvest import harvest_results
from shellous.log import LOGGER

_PROMPT = ">>> "
_PROMPT_ELLIPSIS = "... "


class Prompt:
    "Utility class to help with an interactive prompt."

    def __init__(self, stdin, stdout, errbuf):
        self.stdin = stdin
        self.stdout = stdout
        self.errbuf = errbuf
        self.prompt_bytes = _PROMPT.encode("utf-8")

    async def prompt(self, input_text=""):
        "Write some input text to stdin, then await the response."
        if input_text:
            self.stdin.write(input_text.encode("utf-8") + b"\n")

        # Drain our write to stdin, and wait for prompt from stdout.
        cancelled, (out, _) = await harvest_results(
            self.stdout.readuntil(self.prompt_bytes),
            self.stdin.drain(),
            timeout=2.0,
        )
        if cancelled:
            raise asyncio.CancelledError()

        # If there are ellipsis bytes in the beginning of out, remove them.
        out = re.sub(rb"^(?:\.\.\. )+", b"", out)

        # Combine stderr and stdout, then clear stderr.
        buf = self.errbuf + out
        self.errbuf.clear()

        # Clean up the output to remove the prompt, then return as string.
        buf = buf.replace(b"\r\n", b"\n")
        assert buf.endswith(self.prompt_bytes)
        promptlen = len(self.prompt_bytes)
        buf = buf[0:-promptlen].rstrip(b"\n")

        return buf.decode("utf-8")


async def run_asyncio_repl(cmds, logfile=None):
    "Helper function to run the asyncio REPL and feed it commands."
    errbuf = bytearray()
    repl = (
        sh(sys.executable, "-m", "asyncio")
        .stdin(sh.CAPTURE)
        .stderr(errbuf)
        .set(return_result=True, inherit_env=False)
        .env(**_current_env())
    )

    async with repl.run() as run:
        p = Prompt(run.stdin, run.stdout, errbuf)
        await p.prompt()

        # Optionally redirect logging to a file.
        await p.prompt("import shellous.log, logging")
        if logfile:
            await p.prompt("shellous.log.LOGGER.setLevel(logging.DEBUG)")
            await p.prompt(
                f"logging.basicConfig(filename='{logfile}', level=logging.DEBUG)"
            )
        else:
            # I don't want random logging messages to confuse the output.
            await p.prompt("shellous.log.LOGGER.setLevel(logging.ERROR)")

        output = []
        for cmd in cmds:
            LOGGER.info("  repl: %r", cmd)
            output.append(await p.prompt(cmd))
            # Give tasks a chance to get started.
            if ".create_task(" in cmd:
                await asyncio.sleep(0.1)

        run.stdin.close()

    result = run.result()
    assert result.exit_code == 0
    return output


async def test_run_asyncio_repl():
    "Test asyncio REPL with a short list of commands."
    result = await run_asyncio_repl(
        [
            "from shellous import sh",
            'await sh("echo", "hello, world")',
        ]
    )

    assert result == [
        "",
        "'hello, world\\n'",
    ]


def test_parse_readme():
    "Test the parse readme function."
    cmds, _ = _parse_readme("README.md")

    assert cmds == [
        "from shellous import sh",
        'await sh("echo", "hello, world")',
        'await sh("echo", 1, 2, [3, 4, (5, 6)])',
        'echo = sh("echo", "-n")',
        'await echo("abc")',
        "echob = echo.set(encoding=None)",
        'await echob("def")',
        '[line async for line in echo("hi\\n", "there")]',
        'await sh("cat", "does_not_exist")',
        'await sh("cat", "does_not_exist").set(exit_codes={0,1})',
        'await echo("abc").result',
        'cmd = "abc" | sh("wc", "-c")',
        "await cmd",
        "from pathlib import Path",
        'cmd = Path("LICENSE") | sh("wc", "-l")',
        "await cmd",
        'output_file = Path("/tmp/output_file")',
        'cmd = sh("echo", "abc") | output_file',
        "await cmd",
        "output_file.read_bytes()",
        'cmd = sh("echo", "def") >> output_file',
        "await cmd",
        "output_file.read_bytes()",
        'cmd = sh("cat", "does_not_exist").stderr(sh.STDOUT)',
        "await cmd.set(exit_codes={0,1})",
        'cmd = sh("cat", "does_not_exist").stderr(sh.INHERIT)',
        "await cmd",
        'pipe = sh("ls") | sh("grep", "README")',
        "await pipe",
        'cmd = sh("grep", "README", sh("ls"))',
        "await cmd",
        "buf = bytearray()",
        'cmd = sh("ls") | sh("tee", sh("grep", "README").writable | buf) | sh.DEVNULL',
        "await cmd",
        "buf",
        'await sh("sleep", 60).set(timeout=0.1)',
        't = asyncio.create_task(sh("sleep", 60).coro())',
        "t.cancel()",
        "await t",
        'ls = sh("ls").set(pty=shellous.cooked(cols=40, rows=10, echo=False))',
        'await ls("README.md", "CHANGELOG.md")',
        'auditor = lambda phase, info: print(phase, info["runner"].name)',
        "sh_audit = sh.set(audit_callback=auditor)",
        'await sh_audit("echo", "goodbye")',
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="win32")
async def test_readme(tmp_path):
    "Test that the REPL commands in the README.md file actually work."

    cmds, outputs = _parse_readme("README.md")

    try:
        logfile = tmp_path / "test_readme.log"
        results = await run_asyncio_repl(cmds, logfile)

        # Compare known outputs to actual results.
        for i, output in enumerate(outputs):
            _check_result(output, results[i])

    except BaseException:
        # If there is any failure, dump the log to stdout.
        print(">" * 10, "LOGFILE", ">" * 10)
        print(logfile.read_text(), end="")
        print("<" * 30)
        raise


def _parse_readme(filename):
    """Scan a readme file for all python-repl code blocks.

    Returns a list of commands and their corresponding outputs.
    """
    with open(filename, encoding="utf-8") as afile:
        data = afile.read()

    # Make a list of all the pycon code blocks in the file.
    _PYTHON_REPL = re.compile(r"\n```pycon\n(.+?)```\n", re.DOTALL)
    blocks = [m.group(1) for m in _PYTHON_REPL.finditer(data)]

    # Break each block into lines.
    lines = []
    for block in blocks:
        lines.extend(block.rstrip().split("\n"))

    # Separate commands from their their corresponding outputs.
    cmds = []
    outputs = []
    for cmd, output in _separate_cmds_and_outputs(lines):
        cmds.append(cmd)
        outputs.append(output)

    return (cmds, outputs)


def _separate_cmds_and_outputs(lines):
    "Generator to separate commands from outputs."
    cmd = None
    output = ""

    for line in lines:
        if line.startswith(_PROMPT):
            if cmd is not None:
                yield (cmd, output.rstrip("\n"))
            cmd = line[4:]
            output = ""
        elif line.startswith(_PROMPT_ELLIPSIS):
            assert cmd
            cmd += "\n" + line[4:]
        else:
            output += f"{line}\n"

    yield (cmd, output.rstrip("\n"))


def _current_env():
    "Return dictionary of current environment minus PYTHONASYNCIODEBUG if set."
    env = os.environ.copy()
    env.pop("PYTHONASYNCIODEBUG", None)
    return env


def _check_result(output, result):
    "Fail if result does not match pattern."

    # The result of the `wc` command has platform-dependent number of spaces.
    # Linux: '3\n'  MacOS: '       3\n'
    WCOUT = re.compile(r"'\s*\d+\\n'")
    if WCOUT.fullmatch(result) and WCOUT.fullmatch(output):
        output_value = int(output[1:-3].strip())
        result_value = int(result[1:-3].strip())
        if result_value == output_value:
            return

    # The result of the pty test has platform-dependent \t vs spaces. There
    # may be ansi color directives on Alpine linux.
    ESC = r"(?:\\x1b[\[0-9;]+m)?"
    PTYOUT = re.compile(
        rf"'{ESC}CHANGELOG.md{ESC}(?:\s+|\\t){ESC}README.md{ESC}\\r\\n'"
    )
    if PTYOUT.fullmatch(result) and PTYOUT.fullmatch(output):
        return

    # cat's stderr is displayed with full path name on Linux/Windows:
    if "/usr/bin/cat:" in result:
        result = result.replace("/usr/bin/cat", "cat")
    elif "/bin/cat:" in result:
        result = result.replace("/bin/cat", "cat")
    # Normalize error message on alpine linux.
    if "can't open 'does_not_exist'" in result:
        result = result.replace("can't open 'does_not_exist'", "does_not_exist")
        result = result.replace('"', "'")
    # Fix TimeoutError for Python 3.11a4.
    if "asyncio.exceptions.TimeoutError" in result:
        result = result.replace("asyncio.exceptions.TimeoutError", "TimeoutError")
    # Make CancelledError more readable.
    if "concurrent.futures._base.CancelledError" in result:
        result = result.replace(
            "concurrent.futures._base.CancelledError", "CancelledError"
        )

    pattern = re.escape(output).replace(r"\.\.\.", ".*")
    if not re.fullmatch(pattern, result, re.DOTALL):
        msg = f"result does not match pattern\n\nresult={result}\n\npattern={output}\n"
        pytest.fail(msg)
