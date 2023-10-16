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
        cli = Prompt(
            run,
            default_prompt="prompt> ",
            default_timeout=10.0,
        )

        await cli.receive(prompt="Name: ")
        await cli.send("friend", prompt="Password: ")
        await cli.send("abc123", noecho=True)

        # Send a command and print the response.
        response = await cli.send("arbitrary")
        print(response)

        cli.close()

    run.result()


logging.basicConfig(level=logging.DEBUG)
asyncio.run(main())
