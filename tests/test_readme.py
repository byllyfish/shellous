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

    runner = repl.runner()
    async with runner as (stdin, stdout, _stderr):
        p = Prompt(stdin, stdout, errbuf)
        await p.prompt()

        output = []
        for cmd in cmds:
            output.append(await p.prompt(cmd))

        stdin.close()

    result = runner.result()
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
        'cmd = Path("README.md") | sh("wc", "-l")',
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
        "async for line in pipe:\n  print(line.rstrip())\n",
        "runner = pipe.runner()",
        "async with runner as (stdin, stdout, stderr):\n"
        "  data = await stdout.readline()\n"
        "  print(data)\n",
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
        assert re.fullmatch(pattern, results[i], re.DOTALL)


def _parse_readme(filename):
    """Scan a readme file for all python-repl code blocks.

    Returns a list of commands and their corresponding outputs.
    """
    with open(filename) as afile:
        data = afile.read()

    # Make a list of all the python-repl code blocks in the file.
    PYTHON_REPL = re.compile(r"\n```python-repl\n(.+?)```\n", re.DOTALL)
    blocks = [m.group(1) for m in PYTHON_REPL.finditer(data)]

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
