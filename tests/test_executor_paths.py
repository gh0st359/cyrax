import pytest

from tools.executor import ToolExecutor
from utils.safety import ScopeEnforcer


@pytest.mark.unit
def test_write_and_read_allowed_relative_path(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))

    write_result = executor.write_file("notes/a.txt", "hello")
    assert write_result.success

    read_result = executor.read_file("notes/a.txt")
    assert read_result.success
    assert read_result.stdout == "hello"


@pytest.mark.unit
def test_rejects_parent_traversal_path(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))

    write_result = executor.write_file("../.ssh/id_rsa", "secret")
    assert not write_result.success
    assert "Rejected path outside work directory" in write_result.stderr

    read_result = executor.read_file("../.ssh/id_rsa")
    assert not read_result.success
    assert "Rejected path outside work directory" in read_result.stderr


@pytest.mark.unit
def test_rejects_absolute_path(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))

    write_result = executor.write_file("/etc/passwd", "nope")
    assert not write_result.success
    assert "Rejected path outside work directory" in write_result.stderr

    read_result = executor.read_file("/etc/passwd")
    assert not read_result.success
    assert "Rejected path outside work directory" in read_result.stderr


@pytest.mark.unit
def test_allows_absolute_path_with_scope(tmp_path):
    scoped_dir = tmp_path / "scoped"
    scoped_dir.mkdir()
    target_file = scoped_dir / "notes.txt"
    target_file.write_text("hello")

    executor = ToolExecutor(
        work_dir=str(tmp_path / "work"),
        scope_enforcer=ScopeEnforcer([str(scoped_dir)]),
    )

    read_result = executor.read_file(str(target_file))

    assert read_result.success
    assert read_result.stdout == "hello"
