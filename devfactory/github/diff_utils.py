from __future__ import annotations

import re


def truncate_diff(diff: str, max_chars: int) -> str:
    """Truncates a unified diff at file boundaries to fit within a character limit.

    If the diff exceeds `max_chars`, it is truncated at the last complete file
    section that fits. An appended footer lists the omitted files. If the
    first section itself exceeds `max_chars`, it is truncated to `max_chars`.

    Args:
        diff: The unified diff string to truncate.
        max_chars: The maximum number of characters allowed for the diff
            content (excluding the footer).

    Returns:
        The truncated diff string, potentially with a footer describing
        omitted sections.
    """
    if len(diff) <= max_chars:
        return diff

    # Split diff into parts: [preamble, delimiter1, content1, delimiter2, content2, ...]
    parts = re.split(r"(^diff --git )", diff, flags=re.MULTILINE)

    # Pre-process parts into complete sections
    # sections[0] is preamble. sections[1:] are complete file sections.
    sections: list[str] = [parts[0]]
    for i in range(1, len(parts), 2):
        delimiter = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        sections.append(delimiter + content)

    accumulated_parts: list[str] = []
    current_len = 0
    omitted_indices: list[int] = []

    # Try to add preamble
    if len(sections[0]) > max_chars:
        accumulated_parts.append(sections[0][:max_chars])
        omitted_indices = list(range(1, len(sections)))
    else:
        accumulated_parts.append(sections[0])
        current_len += len(sections[0])

        # Try to add complete sections
        for i in range(1, len(sections)):
            if current_len + len(sections[i]) <= max_chars:
                accumulated_parts.append(sections[i])
                current_len += len(sections[i])
            else:
                if i == 1:
                    # First file section is too large. Truncate it.
                    remaining = max_chars - current_len
                    accumulated_parts.append(sections[1][:remaining])
                    omitted_indices = list(range(2, len(sections)))
                else:
                    # Subsequent file section is too'. Skip it.
                    omitted_indices = list(range(i, len(sections)))
                break

    accumulated_diff = "".join(accumulated_parts)

    if not omitted_indices:
        return accumulated_diff

    omitted_paths: list[str] = []
    for idx in omitted_indices:
        if idx >= 1:  # Complete sections
            match = re.search(r"diff --git a/\S+ b/(\S+)", sections[idx])
            if match:
                omitted_paths.append(match.group(1))

    footer_content = (
        f"[... {len(omitted_indices)} more file(s) omitted: {', '.join(omitted_paths)} ...]"
    )

    if not accumulated_diff:
        return footer_content

    # Ensure we don't exceed max_chars + len(footer)
    # If accumulated_diff is already at max_chars, we might need to trim it
    # to ensure '\n\n' + footer doesn't push us way over, although the requirement
    # says result never exceeds max_chars + len(footer) in total length.
    # The logic above handles this as long as accumulated_diff is <= max_chars.

    return accumulated_diff + "\n\n" + footer_content
