"Example program that runs the fake_prompter CLI."

import asyncio
import logging
import sys
from pathlib import Path

from shellous import sh
from shellous.prompt import Prompt

_DIR = Path(__file__).parent


async def main():
    opts = ()
    if len(sys.argv) == 2:
        opts = (sys.argv[1],)

    cmd = sh(_DIR / "fake_prompter.sh", opts).stdin(sh.CAPTURE).stdout(sh.CAPTURE)

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

    run.result()


logging.basicConfig(level=logging.DEBUG)
asyncio.run(main())
