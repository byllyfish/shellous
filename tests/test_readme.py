"Test the asyncio REPL calls in the README (MacOS only)."

import asyncio
import os
import re
import sys

import pytest
import shellous

pytestmark = pytest.mark.asyncio

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
        out, _ = await asyncio.gather(
            self.stdout.readuntil(self.prompt_bytes),
            self.stdin.drain(),
        )

        # If there are ellipsis bytes in the beginning of out, remove them.
        out = re.sub(br"^(?:\.\.\. )+", b"", out)

        # Combine stderr and stdout, then clear stderr.
        buf = self.errbuf + out
        self.errbuf.clear()

        # Clean up the output to remove the prompt, then return as string.
        buf = buf.replace(b"\r\n", b"\n")
        assert buf.endswith(self.prompt_bytes)
        promptlen = len(self.prompt_bytes)
        buf = buf[0:-promptlen].rstrip(b"\n")

        return buf.decode("utf-8")


async def run_asyncio_repl(cmds):
    "Helper function to run the asyncio REPL and feed it commands."
    sh = shellous.context()

    errbuf = bytearray()
    repl = (
        sh(sys.executable, "-m", "asyncio")
        .stdin(shellous.CAPTURE)
        .stderr(errbuf)
        .set(return_result=True, inherit_env=False)
        .env(**_current_env())
    )

    async with repl.run() as run:
        p = Prompt(run.stdin, run.stdout, errbuf)
        await p.prompt()

        # Turn off WARNING logging.
        await p.prompt("import shellous.log, logging")
        await p.prompt("shellous.log.LOGGER.setLevel(logging.ERROR)")

        output = []
        for cmd in cmds:
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
            "import shellous",
            "sh = shellous.context()",
            'await sh("echo", "hello, world")',
            'await sh("cat", "does_not_exist").stderr(shellous.STDOUT).set(exit_codes={0,1})',
        ]
    )

    assert result == [
        "",
        "",
        "'hello, world\\n'",
        "'cat: does_not_exist: No such file or directory\\n'",
    ]


def test_parse_readme():
    "Test the parse readme function."
    cmds, _ = _parse_readme("README.md")

    assert cmds == [
        "import shellous",
        "sh = shellous.context()",
        'await sh("echo", "hello, world")',
        'echo = sh("echo", "-n")',
        'await echo("abc")',
        'await echo("abc").set(return_result=True)',
        'await sh("cat", "does_not_exist")',
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
        'cmd = sh("cat", "does_not_exist").stderr(shellous.STDOUT)',
        "await cmd.set(exit_codes={0,1})",
        'cmd = sh("cat", "does_not_exist").stderr(shellous.INHERIT)',
        "await cmd",
        'pipe = sh("ls") | sh("grep", "README")',
        "await pipe",
        'cmd = sh("grep", "README", sh("ls"))',
        "await cmd",
        "buf = bytearray()",
        'cmd = sh("ls") | sh("tee", ~sh("grep", "README") | buf) | shellous.DEVNULL',
        "await cmd",
        "buf",
        "async with pipe.run() as run:\n"
        "  data = await run.stdout.readline()\n"
        "  print(data)\n",
        "async with pipe.run() as run:\n"
        "  async for line in run:\n"
        "    print(line.rstrip())\n",
        'sleep = sh("sleep", 60).set(incomplete_result=True)',
        "t = asyncio.create_task(sleep.coro())",
        "t.cancel()",
        "await t",
        'ls = sh("ls").set(pty=shellous.canonical(cols=20, rows=10, echo=False))',
        'await ls("README.md", "CHANGELOG.md")',
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin")
async def test_readme():
    "Test that the REPL commands in the README.md file actually work."

    cmds, outputs = _parse_readme("README.md")
    results = await run_asyncio_repl(cmds)

    # Compare known outputs to actual results.
    for i, output in enumerate(outputs):
        # Replace ... with .*?
        pattern = re.escape(output)
        pattern = pattern.replace(r"\.\.\.", ".*")
        if not re.fullmatch(pattern, results[i], re.DOTALL):
            msg = f"result does not match pattern\n\nresult={results[i]}\n\npattern={output}\n"
            pytest.fail(msg)


def _parse_readme(filename):
    """Scan a readme file for all python-repl code blocks.

    Returns a list of commands and their corresponding outputs.
    """
    with open(filename) as afile:
        data = afile.read()

    # Make a list of all the python-repl code blocks in the file.
    _PYTHON_REPL = re.compile(r"\n```python-repl\n(.+?)```\n", re.DOTALL)
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
