import pytest

from cyrax import _find_all_actions


@pytest.mark.unit
def test_find_all_actions_parses_execute_spawn_and_write_file_in_order():
    response = """
Plan:
[WRITE_FILE path="scan.py"]print('ok')[/WRITE_FILE]
Then run it.
[EXECUTE]python scan.py[/EXECUTE]
And parallelize.
[SPAWN type="recon"]Enumerate subdomains[/SPAWN]
"""

    actions = _find_all_actions(response)

    assert [kind for _, kind, _ in actions] == ["write_file", "execute", "spawn"]
    assert actions[0][2].group(1) == "scan.py"
    assert actions[1][2].group(1).strip() == "python scan.py"
    assert actions[2][2].group(1) == "recon"
    assert actions[2][2].group(2).strip() == "Enumerate subdomains"
