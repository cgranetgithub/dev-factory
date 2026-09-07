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

    # Process file sections until we can't fit another one
    i = 1  # Start from index 1 since index 0 is preamble or empty
    omitted_files = []

    while i < len(sections):
        section = "diff --git " + sections[i]
        section_len = len(section)

        # Check if we can add this entire section without exceeding limit
        if total_length + section_len <= max_chars:
            result_lines.append(section)
            total_length += section_len
            i += 1
        else:
            # Cannot include complete file - check edge case first
            if len(result_lines) == 0:
                # No files included yet, so truncate the next section
                truncated_section = section[:max_chars]
                result_lines.append(truncated_section)
                omitted_files.append(_extract_filename(section))
            else:
                # Include the remaining sections as omitted
                for j in range(i, len(sections)):
                    if sections[j]:  # Only if there's content
                        omitted_files.append(_extract_filename("diff --git " + sections[j]))

            break

    # Add footer for omitted files if any
    if omitted_files:
        omitted_count = len(omitted_files)
        footer = f"[... {omitted_count} more file(s) omitted: {', '.join(omitted_files)}]"
        result_lines.append(footer)

    return "\n".join(result_lines)


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
                return parts[2][2:]  # Remove leading 'b/' prefix
    return "unknown"
