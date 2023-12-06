"""Example program that runs the fake_prompter CLI.

This example uses the legacy/original API.
"""

import asyncio
import logging
from pathlib import Path

from shellous import sh
from shellous.prompt import Prompt

_DIR = Path(__file__).parent
_FAKE_PROMPTER = _DIR / "fake_prompter.sh"


async def main():
    cmd = sh(_FAKE_PROMPTER).stdin(sh.CAPTURE).stdout(sh.CAPTURE)

    async with cmd.set(pty=True) as run:
        cli = Prompt(run, default_timeout=10.0)

        await cli.expect("Name: ")
        await cli.send("friend")
        await cli.expect("Password: ")
        await cli.send("abc123", no_echo=True)
        await cli.expect("prompt> ")

        # Send a command and print the response.
        await cli.send("arbitrary")
        response, _ = await cli.expect("prompt> ")
        print(response)

        cli.close()

    run.result()  # To check exit status...


logging.basicConfig(level=logging.DEBUG)
asyncio.run(main())
