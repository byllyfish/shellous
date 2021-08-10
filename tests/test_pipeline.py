from pathlib import Path

import pytest
from shellous import context, pipeline
from shellous.command import Command
from shellous.pipeline import Pipeline


@pytest.fixture
def sh():
    return context()


def test_empty_pipeline(sh):
    with pytest.raises(ValueError, match="must include at least one command"):
        pipe = pipeline()


def test_pipeline_cmd(sh):
    pipe = pipeline(sh("cmd1"), sh("cmd2"))
    assert pipe.commands == (sh("cmd1"), sh("cmd2"))


def test_pipeline_cmd_append(sh):
    pipe = pipeline(sh("cmd1").stdin("a"), sh("cmd2").stdout("b", append=True))
    assert pipe.commands == (sh("cmd1").stdin("a"), sh("cmd2").stdout("b", append=True))


def test_pipeline(sh):
    pipe = pipeline(sh("echo")) | sh("cat")
    assert pipe.commands == (sh("echo"), sh("cat"))


def test_pipeline_unsupported_rhs(sh):
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \|"):
        pipe = pipeline(sh("echo")) | 43


def test_pipeline_unsupported_lhs(sh):
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \|"):
        pipe = 44 | pipeline(sh("echo"))


def test_pipeline_unsupported_rhs_append(sh):
    with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for >>"):
        pipe = pipeline(sh("echo")) >> 43


def test_pipeline_input(sh):
    pipe = "random input" | pipeline(sh("echo"))
    assert pipe.commands == (sh("echo").stdin("random input"),)


def test_pipeline_output(sh):
    pipe = pipeline(sh("echo")) | "/tmp/somefile"
    assert pipe.commands == (sh("echo").stdout("/tmp/somefile"),)


def test_pipeline_output_append(sh):
    pipe = pipeline(sh("echo")) >> "/tmp/somefile"
    assert pipe.commands == (sh("echo").stdout("/tmp/somefile", append=True),)


def test_pipeline_full(sh):
    "Test operator overloading in Pipeline only."
    pipe = (
        "/tmp/input"
        | pipeline(sh("cmd1"))
        | sh("cmd2")
        | pipeline(sh("cmd3")) >> "/tmp/output"
    )
    assert pipe.commands == (
        sh("cmd1").stdin("/tmp/input"),
        sh("cmd2"),
        sh("cmd3").stdout("/tmp/output", append=True),
    )


def test_pipeline_pieces(sh):
    input = "/tmp/input" | pipeline(sh("cmd1"))
    output = pipeline(sh("cmd2")) >> "/tmp/output"
    pipe = input | output
    assert pipe.commands == (
        sh("cmd1").stdin("/tmp/input"),
        sh("cmd2").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq(sh):
    pipe1 = pipeline(sh("ls"))
    pipe2 = pipe1
    pipe2 |= sh("grep", ".")
    assert pipe2 is not pipe1
    assert pipe1 == pipeline(sh("ls"))
    assert pipe2 == pipeline(sh("ls"), sh("grep", "."))


def test_pipeline_rshift_eq(sh):
    pipe = pipeline(sh("ls"))
    pipe >>= "/tmp/output"
    assert pipe == pipeline(
        sh("ls").stdout("/tmp/output", append=True),
    )


# The following tests depend on operator overloading in Command.


def test_pipeline_full_commands(sh):
    "Test depends on operator overloading in Command."
    pipe = (
        "/tmp/input"
        | sh("cmd1")
        | sh("cmd2")
        | sh("cmd3")
        | sh("cmd4") >> "/tmp/output"
    )
    assert pipe == pipeline(
        sh("cmd1").stdin("/tmp/input"),
        sh("cmd2"),
        sh("cmd3"),
        sh("cmd4").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq_commands(sh):
    pipe = sh("ls")
    pipe |= sh("grep")
    assert pipe == pipeline(sh("ls"), sh("grep"))


def test_pipeline_rshift_eq_commands(sh):
    pipe = sh("ls") | sh("grep")
    pipe >>= "/tmp/output"
    assert pipe == pipeline(
        sh("ls"),
        sh("grep").stdout("/tmp/output", append=True),
    )


def test_pipeline_or_eq_input_commands(sh):
    pipe = "/tmp/input"
    pipe |= sh("grep")
    pipe |= "/tmp/output"
    assert pipe == pipeline(sh("grep").stdin("/tmp/input").stdout("/tmp/output"))


def test_pipeline_path_input(sh):
    pipe = Path("/tmp/input") | sh("wc")
    assert pipe == pipeline(sh("wc").stdin(Path("/tmp/input")))


def test_pipeline_path_output(sh):
    pipe = sh("wc") | Path("/tmp/output")
    assert pipe == pipeline(sh("wc").stdout(Path("/tmp/output")))


def test_pipeline_vs_command(sh):
    """In the current implementation, commands and pipelines are different
    objects and classes."""
    cmd1 = sh("echo").stdin("abc")
    cmd2 = "abc" | sh("echo")  # single command pipeline
    assert cmd1 != cmd2
    assert isinstance(cmd1, Command)
    assert isinstance(cmd2, Pipeline)
