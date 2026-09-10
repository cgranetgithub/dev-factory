"""Utility functions for handling git diffs."""

from __future__ import annotations

import logging

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

logger = logging.getLogger(__name__)


def truncate_diff(diff: str, max_chars: int) -> str:
    """
    Truncate a git diff at file boundaries instead of arbitrary character indices.

    Args:
        diff: The git diff string to truncate
        max_chars: Maximum number of characters allowed in the result

    Returns:
        The truncated diff with a footer listing omitted files, or the original diff
        if it's already within the limit.
    """
    if not diff or len(diff) <= max_chars:
        return diff

    try:
        patch_set = PatchSet(diff)
    except UnidiffParseError as e:
        # Best effort. The reviewer needs *a* diff more than it needs a perfect one,
        # and an oversized prompt is worse than a short one — but still cut on a line
        # boundary, which is the mistake #60 was filed for.
        logger.warning(f"[diff] unparseable diff ({e}); cutting on the last whole line")
        return diff[:max_chars].rsplit("\n", 1)[0]

    if not patch_set:
        # Nothing that looks like a file. Historically this input is returned
        # untouched rather than cut blindly; keep that.
        return diff

    # unidiff is used as a locator, not as a serialiser: it tells us which line each
    # file starts on and what the file is called, and we slice the original text.
    # Re-emitting `str(patched_file)` would hand the reviewer a reconstruction, and
    # the reviewer is judging the real diff.
    lines = diff.splitlines(keepends=True)
    located = [(f.path, f.diff_line_no) for f in patch_set if f.diff_line_no is not None]
    if not located:
        return diff

    # Each file runs to the start of the next one; the last runs to the end.
    bounds = [start for _, start in located] + [len(lines) + 1]
    sections = [
        (path, "".join(lines[bounds[i] - 1 : bounds[i + 1] - 1]))
        for i, (path, _) in enumerate(located)
    ]

    result_lines = []
    total_length = 0
    omitted_files: list[str] = []

    # Keep the preamble. `get_diff` asks git for `--stat -p`, so a diff opens with
    # the summary of every file it touches — which is precisely the overview a
    # reviewer needs once the body has been cut. Dropping it would remove the part
    # that says what is no longer shown. unidiff discards it, so take it from the
    # raw text: everything above the first file's `diff --git` line.
    preamble = "".join(lines[: bounds[0] - 1])
    if preamble:
        result_lines.append(preamble.rstrip("\n"))
        total_length += len(preamble)

    included_a_file = False

    for i, (_path, section) in enumerate(sections):
        if total_length + len(section) <= max_chars:
            result_lines.append(section.rstrip("\n"))
            total_length += len(section)
            included_a_file = True
            continue

        # Cannot include this file whole.
        if not included_a_file:
            # Nothing but the preamble so far: show what fits of this file rather
            # than nothing at all. It is partially present, so it is NOT reported
            # as omitted — only the files after it are.
            remaining = max(0, max_chars - total_length)
            result_lines.append(section[:remaining].rstrip("\n"))
            omitted_files.extend(name for name, _ in sections[i + 1 :])
        else:
            omitted_files.extend(name for name, _ in sections[i:])
        break

    if omitted_files:
        omitted_count = len(omitted_files)
        footer = f"[... {omitted_count} more file(s) omitted: {', '.join(omitted_files)}]"
        result_lines.append(footer)

    return "\n\n".join(result_lines)
