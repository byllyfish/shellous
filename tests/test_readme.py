"Test the asyncio REPL calls in the README (MacOS only)."

import asyncio
import os
import re
import sys

import pytest

from shellous import sh
from shellous.log import LOGGER
from shellous.prompt import Prompt

_skip_py313 = pytest.mark.skipif(
    sys.version_info >= (3, 13), reason="issue with repl in python 3.13"
)


# Custom Python REPL prompt.
_PROMPT = "<+=+=+>"


async def run_asyncio_repl(cmds, logfile=None):
    "Helper function to run the asyncio REPL and feed it commands."
    repl = (
        sh(sys.executable, "-m", "asyncio")
        .stdin(sh.CAPTURE)
        .stdout(sh.CAPTURE)
        .stderr(sh.STDOUT)
        .set(inherit_env=False)
        .env(**_current_env())
    )

    async with repl as run:
        prompt = Prompt(
            run,
            default_prompt=_PROMPT,
            default_timeout=5.0,
            normalize_newlines=True,
        )

        # Customize the python REPL prompt to make it easier to detect. The
        # initial output of the REPL will include the old ">>> " prompt which
        # we can ignore.
        await prompt.command(f"import sys; sys.ps1 = '{_PROMPT}'; sys.ps2 = ''")

        # Optionally redirect logging to a file.
        await prompt.command("import shellous.log, logging")
        if logfile:
            await prompt.command("shellous.log.LOGGER.setLevel(logging.DEBUG)")
            await prompt.command(
                f"logging.basicConfig(filename='{logfile}', level=logging.DEBUG)"
            )
        else:
            # I don't want random logging messages to confuse the output.
            await prompt.command("shellous.log.LOGGER.setLevel(logging.ERROR)")

        output = []
        for cmd in cmds:
            LOGGER.info("  repl: %r", cmd)
            out = await prompt.command(cmd)
            output.append(out.rstrip("\n"))
            # Give tasks a chance to get started.
            if ".create_task(" in cmd:
                await asyncio.sleep(0.1)

        prompt.close()

    result = run.result()
    assert result.exit_code == 0
    return output


@_skip_py313
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
        "from shellous import Command",
        'def exclaim(word: str) -> Command[str]:\n  return sh("echo", "-n", f"{word}!!")\n',
        'await exclaim("Oh")',
        'await echo.result("abc")',
        'await sh("cat", "does_not_exist")',
        'await sh("cat", "does_not_exist").set(exit_codes={0,1})',
        '[line async for line in echo("hi\\n", "there")]',
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
        'pipe = sh("ls") | sh("grep", "README").result',
        "await pipe",
        "[line.strip() async for line in pipe]",
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
        'await sh("echo", "in a pty").set(pty=True)',
        'ls = sh("ls").set(pty=shellous.cooked(cols=40, rows=10, echo=False))',
        'await ls("README.md", "CHANGELOG.md")',
        'auditor = lambda phase, info: print(phase, info["runner"].name)',
        "sh_audit = sh.set(audit_callback=auditor)",
        'await sh_audit("echo", "goodbye")',
        "rsh = sh.result",
        'await rsh("echo", "whatever")',
        'cmd = sh("echo").set(encoding="latin1")',
        "cmd.options.encoding",
        'cmd = sh("echo").env(ENV1="a", ENV2="b").env(ENV2=3)',
        "cmd.options.env",
    ]


@_skip_py313
@pytest.mark.skipif(sys.platform == "win32", reason="win32")
async def test_readme(tmp_path):
    "Test that the REPL commands in the README.md file actually work."
    cmds, outputs = _parse_readme("README.md")

    logfile = tmp_path / "test_readme.log"
    try:
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
        if line.startswith(">>> "):
            if cmd is not None:
                yield (cmd, output.rstrip("\n"))
            cmd = line[4:]
            output = ""
        elif line.startswith("... "):
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
