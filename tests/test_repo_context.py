"""Tests for repo_context module."""

import tempfile
from pathlib import Path

from devfactory.repo_context import (
    build_context_block,
    build_file_tree,
    find_test_files,
    is_excluded,
    read_files,
)


def make_repo(files: dict[str, str]) -> Path:
    """Create a temp directory with given files."""
    tmp = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp


def test_is_excluded_git():
    assert is_excluded(Path(".git/config"))
    assert is_excluded(Path("src/.git/config"))


def test_is_excluded_pycache():
    assert is_excluded(Path("src/__pycache__/foo.pyc"))


def test_is_excluded_normal_file():
    assert not is_excluded(Path("src/main.py"))


def test_build_file_tree():
    repo = make_repo(
        {
            "src/main.py": "print('hello')",
            "tests/test_main.py": "def test_it(): pass",
            "README.md": "# test",
        }
    )
    tree = build_file_tree(repo)
    assert repo.name in tree
    assert "src" in tree
    assert "main.py" in tree
    assert "__pycache__" not in tree


def test_read_files_existing():
    repo = make_repo({"src/utils.py": "def foo(): pass"})
    result = read_files(repo, ["src/utils.py"])
    assert "src/utils.py" in result
    assert "def foo" in result["src/utils.py"]


def test_read_files_missing():
    repo = make_repo({})
    result = read_files(repo, ["does_not_exist.py"])
    assert result == {}


def test_find_test_files():
    repo = make_repo(
        {
            "tests/test_foo.py": "def test_foo(): pass",
            "tests/test_bar.py": "def test_bar(): pass",
            "src/main.py": "x = 1",
        }
    )
    tests = find_test_files(repo)
    assert len(tests) == 2
    assert all("test_" in t for t in tests)


def test_build_context_block():
    repo = make_repo(
        {
            "src/main.py": "def hello(): pass",
            "tests/test_main.py": "def test_hello(): pass",
        }
    )
    block = build_context_block(repo, files_to_modify=["src/main.py"])
    assert "Repository Context" in block
    assert "File Tree" in block
    assert "def hello" in block
