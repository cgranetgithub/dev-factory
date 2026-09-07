"""Utility functions for handling git diffs."""

from __future__ import annotations


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

    # Split the diff into sections based on file boundaries
    # Note: sections[0] may be empty or contain a preamble; all others are actual files
    sections = diff.split("diff --git ")

    # If there are no sections (unexpected), return as-is
    if not sections or len(sections) <= 1:
        return diff

    result_lines = []
    total_length = 0
    omitted_files: list[str] = []

    # Keep the preamble. `get_diff` asks git for `--stat -p`, so a diff opens with
    # the summary of every file it touches — which is precisely the overview a
    # reviewer needs once the body has been cut. Dropping it would remove the part
    # that says what is no longer shown.
    preamble = sections[0]
    if preamble:
        result_lines.append(preamble.rstrip("\n"))
        total_length += len(preamble)

    # Process file sections until we can't fit another one
    i = 1  # Start from index 1 since index 0 is preamble or empty

    while i < len(sections):
        section = "diff --git " + sections[i]
        section_len = len(section)

        # Check if we can add this entire section without exceeding limit
        if total_length + section_len <= max_chars:
            result_lines.append(section.rstrip("\n"))
            total_length += section_len
            i += 1
        else:
            # Cannot include this file whole.
            if not any(line.startswith("diff --git ") for line in result_lines):
                # Nothing but the preamble so far: show what fits of this file
                # rather than nothing at all. It is partially present, so it is
                # NOT reported as omitted — only the files after it are.
                remaining = max(0, max_chars - total_length)
                result_lines.append(section[:remaining].rstrip("\n"))
                i += 1

            for j in range(i, len(sections)):
                if sections[j]:
                    omitted_files.append(_extract_filename("diff --git " + sections[j]))
            break

    # Add footer for omitted files if any
    if omitted_files:
        omitted_count = len(omitted_files)
        footer = f"[... {omitted_count} more file(s) omitted: {', '.join(omitted_files)}]"
        result_lines.append(footer)

    return "\n\n".join(result_lines)


def _extract_filename(diff_section: str) -> str:
    """
    Extract filename from a diff --git section.

    Args:
        diff_section: A section starting with "diff --git "

    Returns:
        The filename extracted from the section.
    """
    lines = diff_section.split("\n")
    for line in lines:
        if line.startswith("diff --git "):
            # Extract file names - format is like: diff --git a/file.txt b/file.txt
            parts = line.split()
            if len(parts) >= 3:
                return parts[2][2:]  # strip the leading 'a/'
    return "unknown"
