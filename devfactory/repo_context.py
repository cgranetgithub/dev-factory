"""
RepoContext — reads the workspace repo and builds a context package
to inject into agent prompts.

Includes:
  - File tree (respecting .gitignore patterns)
  - Content of specific files (files_to_modify from TaskSpec)
  - Existing test files
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Files/dirs to always exclude from tree and content reads
EXCLUDE_PATTERNS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "*.egg-info",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".DS_Store",
    "*.pyc",
    "*.pyo",
    "devfactory.db",
}

MAX_FILE_BYTES = 12_000  # max chars per file injected into prompt
MAX_TREE_FILES = 80  # max files in tree listing


def is_excluded(path: Path) -> bool:
    for part in path.parts:
        for pattern in EXCLUDE_PATTERNS:
            if "*" in pattern:
                import fnmatch

                if fnmatch.fnmatch(part, pattern):
                    return True
            elif part == pattern:
                return True
    return False


def build_file_tree(repo_path: Path) -> str:
    """Return a compact file tree string, like `tree` output."""
    lines = [f"{repo_path.name}/"]
    files_found = 0

    def _walk(directory: Path, prefix: str = ""):
        nonlocal files_found
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return

        entries = [e for e in entries if not is_excluded(e.relative_to(repo_path))]

        for i, entry in enumerate(entries):
            if files_found >= MAX_TREE_FILES:
                lines.append(f"{prefix}... (truncated)")
                return
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            files_found += 1
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                _walk(entry, prefix + extension)

    _walk(repo_path)
    return "\n".join(lines)


def read_files(repo_path: Path, rel_paths: list[str]) -> dict[str, str]:
    """
    Read file contents for the given relative paths.
    Returns {rel_path: content}, skipping missing/binary files.
    """
    result = {}
    for rel in rel_paths:
        path = repo_path / rel
        if not path.exists():
            logger.debug(f"[repo_context] {rel} not found (will be created)")
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > MAX_FILE_BYTES:
                content = content[:MAX_FILE_BYTES] + f"\n... [truncated at {MAX_FILE_BYTES} chars]"
            result[rel] = content
        except Exception as e:
            logger.warning(f"[repo_context] could not read {rel}: {e}")
    return result


def find_test_files(repo_path: Path) -> list[str]:
    """Find existing test files in the repo."""
    tests = []
    for p in repo_path.rglob("test_*.py"):
        if not is_excluded(p.relative_to(repo_path)):
            tests.append(str(p.relative_to(repo_path)))
    for p in repo_path.rglob("*_test.py"):
        if not is_excluded(p.relative_to(repo_path)):
            tests.append(str(p.relative_to(repo_path)))
    return sorted(tests)[:10]  # cap at 10


def build_context_block(repo_path: Path, files_to_modify: list[str]) -> str:
    """
    Build the full context string to inject into the developer prompt.
    Includes file tree + content of relevant files.
    """
    parts = ["## Repository Context\n"]

    # File tree
    parts.append("### File Tree\n```")
    parts.append(build_file_tree(repo_path))
    parts.append("```\n")

    # Files to modify (existing content)
    if files_to_modify:
        existing = read_files(repo_path, files_to_modify)
        if existing:
            parts.append("### Existing File Contents (files to modify)\n")
            for rel_path, content in existing.items():
                ext = Path(rel_path).suffix.lstrip(".")
                parts.append(f"**`{rel_path}`**\n```{ext}\n{content}\n```\n")

    # Existing tests (for context)
    test_files = find_test_files(repo_path)
    if test_files:
        test_contents = read_files(repo_path, test_files[:3])  # max 3 test files
        if test_contents:
            parts.append("### Existing Tests (for style reference)\n")
            for rel_path, content in test_contents.items():
                parts.append(f"**`{rel_path}`**\n```python\n{content}\n```\n")

    return "\n".join(parts)
