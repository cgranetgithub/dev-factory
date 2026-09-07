"""
Diff utility functions for truncating git diffs at file boundaries.
"""

from __future__ import annotations


def truncate_diff(diff: str, max_chars: int) -> str:
    """
    Truncate a git diff to a maximum number of characters, preserving complete file sections.

    If the diff is already shorter than or equal to max_chars, it's returned unchanged.
    For diffs longer than max_chars, the function truncates at file boundaries
    (starting with 'diff --git '), preserving complete file sections.

    Args:
        diff: The git diff string to truncate
        max_chars: Maximum allowed characters in the result

    Returns:
        The truncated diff string with optional footer indicating omitted files,
        or the original diff if it's already within the limit
    """
    # If the diff is already short enough, return as-is
    if len(diff) <= max_chars:
        return diff

    # Split on 'diff --git ' to identify file sections
    parts = diff.split("diff --git ")

    # If there's only one part (no 'diff --git ' markers), it's not a standard git diff
    # Truncate normally with footer
    if len(parts) <= 1:
        return diff[:max_chars] + "\n\n[... diff truncated for context limit ...]"

    # Start with first part (should be the header, may be empty)
    result_parts = [parts[0]]
    result_length = len(parts[0])

    # Process each file section
    for i in range(1, len(parts)):
        # Reconstruct file section by adding back "diff --git "
        file_section = f"diff --git {parts[i]}"

        # Calculate potential length if we include this section
        potential_length = result_length + len(file_section)

        # If adding this would exceed the limit...
        if potential_length > max_chars:
            # If we're at the first file and it alone already exceeds max_chars,
            # just truncate it
            if i == 1 and len(file_section) > max_chars:
                return diff[:max_chars] + "\n\n[... diff truncated for context limit ...]"

            # Otherwise, stop here and add footer indicating how many files were omitted
            omitted_count = len(parts) - i
            if omitted_count > 0:
                footer = f"\n\n[... {omitted_count} more file(s) omitted ...]"
                return "".join(result_parts) + footer

        # Add this file section to result
        result_parts.append(file_section)
        result_length += len(file_section)

    # If we didn't hit the limit, return as-is
    return "".join(result_parts)
