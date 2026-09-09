"""
The addresses the project publishes must be real.

Issue #53. The scaffolding placeholder organisation survived into the package
metadata, the README and every pull request the factory opened — the first
thing a visitor clicks, and a 404. These keep it from coming back.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from devfactory.github.pr import PROJECT_URL

_ROOT = Path(__file__).resolve().parent.parent
# Assembled so that a grep for the placeholder finds nothing in the repository,
# this file included — that is the acceptance criterion.
_PLACEHOLDER = "your-" + "org"


def test_the_package_urls_point_at_the_real_repository():
    with (_ROOT / "pyproject.toml").open("rb") as f:
        urls = tomllib.load(f)["project"]["urls"]

    assert urls["Homepage"] == PROJECT_URL
    assert urls["Issues"] == f"{PROJECT_URL}/issues"


def test_no_placeholder_organisation_survives_anywhere_a_visitor_reads():
    for name in ("README.md", "CONTRIBUTING.md", "pyproject.toml"):
        text = (_ROOT / name).read_text(encoding="utf-8")
        assert _PLACEHOLDER not in text, f"{name} still carries the scaffolding placeholder"


def test_the_clone_command_matches_the_repository_name():
    """`git clone .../dev-factory.git` creates `dev-factory/`; a `cd devfactory`
    on the next line fails for everyone who copies the block."""
    for name in ("README.md", "CONTRIBUTING.md"):
        text = (_ROOT / name).read_text(encoding="utf-8")
        assert f"git clone {PROJECT_URL}.git\ncd dev-factory" in text, name
