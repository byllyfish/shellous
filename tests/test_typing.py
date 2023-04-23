from pathlib import Path

from typing_extensions import assert_type

from shellous import CmdContext, Command, Result, sh


async def typecheck_cmdcontext() -> None:
    "Test typing in CmdContext class."
    assert_type(sh, CmdContext[str])
    assert_type(sh.result, CmdContext[Result])
    assert_type(sh.set(inherit_env=False), CmdContext[str])
    assert_type(sh.result.set(inherit_env=False), CmdContext[Result])


async def typecheck_command() -> None:
    "Test typing in Command class."
    out = await sh("echo", "abc")
    assert_type(out, str)

    out = await sh.result("echo", "abc")
    assert_type(out, Result)

    out = await sh("echo", "abc").result
    assert_type(out, Result)

    cmd = sh("echo")
    assert_type(cmd, Command[str])
    out = await cmd("abc")
    assert_type(out, str)

    cmd = cmd.result
    assert_type(cmd, Command[Result])
    out = await cmd("abc")
    assert_type(out, Result)

    out = await cmd.set(exit_codes={0})
    assert_type(out, Result)


async def typecheck_command_concat():
    "Test typing when concatenating Commands."
    cmd1 = sh("echo1")
    cmd2 = sh.result("echo2")

    assert_type(cmd1 % cmd2, Command[str])
    assert_type(cmd2 % cmd1, Command[Result])


async def typecheck_command_redirect():
    "Test typing when redirecting commands."
    tmp = Path("/tmp")

    cmd1 = sh("echo1")
    assert_type("foo" | cmd1, Command[str])
    assert_type(cmd1 | bytearray(), Command[str])
    assert_type(cmd1 >> tmp, Command[str])

    cmd2 = sh.result("echo2")
    assert_type("foo" | cmd2, Command[Result])
    assert_type(cmd2 | bytearray(), Command[Result])
    assert_type(cmd2 >> tmp, Command[Result])
