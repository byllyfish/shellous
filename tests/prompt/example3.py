"""Example program that runs the fake_prompter CLI.

This example uses the new simplified API.
"""

import asyncio
import logging
import re
from pathlib import Path

from shellous import sh

_DIR = Path(__file__).parent
_FAKE_PROMPTER = _DIR / "fake_prompter.sh"


async def main():
    cmd = sh(_FAKE_PROMPTER, "--check").set(pty=True)

    async with cmd.prompt(timeout=10.0) as cli:
        # Read various prompts and send a response.
        while True:
            _, m = await cli.expect(re.compile(r"\[Yn\] |Name: |Password: |prompt> "))
            token = m[0] if m else ""
            if token == "Name: ":
                await cli.send("friend")
            elif token == "Password: ":
                await cli.send("abc123", no_echo=True)
            elif token == "[Yn] ":
                await cli.send("Y")
            else:
                break

        assert token == "prompt> "

        # Send a command and print the response.
        response = await cli.command("arbitrary", prompt="prompt> ")
        print(response)


logging.basicConfig(level=logging.DEBUG)
asyncio.run(main())
