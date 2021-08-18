"Test the asyncio REPL calls in the README."

import asyncio
import re
import sys

import pytest
import shellous

pytestmark = pytest.mark.asyncio

_PROMPT = ">>> "


class Prompt:
    "Utility class to help with an interactive prompt."

    def __init__(self, stdin, stdout, stderr):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.prompt_bytes = _PROMPT.encode("utf-8")

    async def prompt(self, input_text=""):
        "Write some input text to stdin, then await the response."
        if input_text:
            assert "\n" not in input_text
            self.stdin.write(input_text.encode("utf-8") + b"\n")

        # Create task to read from stderr.
        task = asyncio.create_task(self._read_stderr())

        # Drain our write to stdin, and wait for prompt from stdout.
        out, _ = await asyncio.gather(
            self.stdout.readuntil(self.prompt_bytes),
            self.stdin.drain(),
        )

        # Cancel read on stderr and await its value.
        task.cancel()
        err = await task

        # Clean up the output to remove the prompt, then return as string.
        buf = err + out
        assert buf.endswith(self.prompt_bytes)
        buf = buf[0 : -len(self.prompt_bytes)].rstrip(b"\r\n")

        return buf.decode("utf-8")

    async def _read_stderr(self):
        "Read data from stderr until cancelled or EOF."
        bufs = []
        try:
            while not self.stderr.at_eof():
                bufs.append(await self.stderr.read(1024))
        except asyncio.CancelledError:
            pass
        return b"".join(bufs)


async def run_asyncio_repl(cmds):
    "Helper function to run the asyncio REPL and feed it commands."
    sh = shellous.context()
    repl = (
        sh(sys.executable, "-m", "asyncio")
        .stdin(shellous.CAPTURE)
        .stderr(shellous.CAPTURE)
        .set(return_result=True)
    )

    runner = repl.runner()
    async with runner as (stdin, stdout, stderr):
        p = Prompt(stdin, stdout, stderr)
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
            'await sh("cat", "does_not_exist").stderr(shellous.STDOUT).set(allowed_exit_codes={0,1})',
        ]
    )

    assert result == [
        "",
        "",
        "'hello, world\\n'",
        "'cat: does_not_exist: No such file or directory\\n'",
    ]


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
        else:
            output += f"{line}\n"

    yield (cmd, output.rstrip("\n"))
