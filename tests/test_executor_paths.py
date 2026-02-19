from tools.executor import ToolExecutor


def test_write_and_read_allowed_relative_path(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))

    write_result = executor.write_file("notes/a.txt", "hello")
    assert write_result.success

    read_result = executor.read_file("notes/a.txt")
    assert read_result.success
    assert read_result.stdout == "hello"


def test_rejects_parent_traversal_path(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))

    write_result = executor.write_file("../.ssh/id_rsa", "secret")
    assert not write_result.success
    assert "Rejected path outside work directory" in write_result.stderr

    read_result = executor.read_file("../.ssh/id_rsa")
    assert not read_result.success
    assert "Rejected path outside work directory" in read_result.stderr


def test_rejects_absolute_path(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))

    write_result = executor.write_file("/etc/passwd", "nope")
    assert not write_result.success
    assert "Rejected path outside work directory" in write_result.stderr

    read_result = executor.read_file("/etc/passwd")
    assert not read_result.success
    assert "Rejected path outside work directory" in read_result.stderr
