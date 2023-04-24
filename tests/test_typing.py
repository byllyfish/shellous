from pathlib import Path

from typing_extensions import assert_type

from shellous import CmdContext, Command, Pipeline, Result, sh


async def test_cmdcontext():
    "Test typing in CmdContext class."
    assert_type(sh, CmdContext[str])
    assert_type(sh.result, CmdContext[Result])
    assert_type(sh.set(inherit_env=False), CmdContext[str])
    assert_type(sh.result.set(inherit_env=False), CmdContext[Result])


async def test_command():
    "Test typing in Command class."
    out = await sh("echo", "abc")
    assert_type(out, str)
    assert isinstance(out, str)

    out = await sh.result("echo", "abc")
    assert_type(out, Result)
    assert isinstance(out, Result)

    out = await sh("echo", "abc").result
    assert_type(out, Result)
    assert isinstance(out, Result)

    cmd = sh("echo")
    assert_type(cmd, Command[str])
    out = await cmd("abc")
    assert_type(out, str)
    assert isinstance(out, str)

    cmd = cmd.result
    assert_type(cmd, Command[Result])
    out = await cmd("abc")
    assert_type(out, Result)
    assert isinstance(out, Result)

    out = await cmd.set(exit_codes={0})
    assert_type(out, Result)
    assert isinstance(out, Result)


async def test_command_concat():
    "Test typing when concatenating Commands."
    cmd1 = sh("echo1")
    cmd2 = sh.result("echo2")

    assert_type(cmd1 % cmd2, Command[str])
    assert_type(cmd2 % cmd1, Command[Result])


async def test_command_redirect():
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


async def test_pipeline():
    "Test typing with Pipeline."
    out = await (sh("echo") | sh("cat") | sh("cat"))
    assert_type(out, str)
    assert isinstance(out, str)

    out = await (sh("echo") | sh("cat") | sh("cat")).result
    assert_type(out, Result)
    assert isinstance(out, Result)

    # If any command is .result, then the Pipeline output is .result.
    out = await (sh.result("echo") | sh("cat") | sh("cat"))
    assert_type(out, str)
    assert isinstance(out, str)

    out = await (sh("echo") | sh.result("cat") | sh("cat"))
    assert_type(out, str)
    assert isinstance(out, str)

    out = await (sh("echo") | sh("cat") | sh.result("cat"))
    assert_type(out, Result)
    assert isinstance(out, Result)


async def test_pipeline_algebra():
    "Test typing with Pipeline."
    pipe1 = sh("echo") | sh("cat") | sh("cat")
    assert_type(pipe1, Pipeline[str])

    pipe2 = sh.result("echo") | sh("cat") | sh("cat")
    assert_type(pipe2, Pipeline[str])

    pipe3 = sh("echo") | sh.result("cat") | sh("cat")
    assert_type(pipe3, Pipeline[str])

    pipe4 = sh("echo") | sh("cat") | sh.result("cat")
    assert_type(pipe4, Pipeline[Result])

    pipe5 = pipe4 | pipe2
    assert_type(pipe5, Pipeline[str])

    pipe6 = pipe1 | pipe4
    assert_type(pipe6, Pipeline[Result])

    pipe1 |= pipe4
    assert_type(pipe1, Pipeline[Result])


async def test_pipeline_redirects():
    "Test typing with pipeline redirects."
    pipe1 = sh("echo") | sh("cat") | sh("cat")
    assert_type("abc" | pipe1, Pipeline[str])
    assert_type(pipe1 | bytearray(), Pipeline[str])
    assert_type("abc" | pipe1 | bytearray(), Pipeline[str])
    assert_type("abc" | pipe1.result | bytearray(), Pipeline[Result])
    assert_type("abc" | pipe1 | sh("cat").result | bytearray(), Pipeline[Result])

    tmp = Path("/tmp")
    assert_type(pipe1 >> tmp, Pipeline[str])
    assert_type(pipe1 | sh("cat").result >> tmp, Pipeline[Result])
