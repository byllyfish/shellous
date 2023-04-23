from typing_extensions import assert_type

from shellous import Command, Result, sh


async def typecheck_command() -> None:
    "Test typing in Command class."
    out = await sh("echo", "abc")
    assert_type(out, str)

    out = await sh.result("echo", "abc")
    assert_type(out, Result)

    out = await sh("echo", "abc").result
    assert_type(out, Result)

    cmd = sh("echo")
    assert_type(cmd, Command)
    out = await cmd("abc")
    assert_type(out, str)

    cmd = cmd.result
    assert_type(cmd, Command)
    out = await cmd("abc")
    assert_type(out, Result)

    out = await cmd.set(exit_codes={0})
    assert_type(out, Result)
