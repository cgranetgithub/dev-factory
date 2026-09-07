from __future__ import annotations

import re


def truncate_diff(diff: str, max_chars: int) -> str:
    """Truncate a unified diff on file boundaries instead of mid-line.

    Args:
        diff: The unified diff string to truncate.
        max_chars: The maximum number of characters to allow before the footer.

    Returns:
        The truncated diff string, optionally with a
        a footer naming omitted files.
    """
    if len(diff) <= max_chars:
        return diff

    marker = "diff --git "
    # Find all starting positions of "diff --git "
    marker_indices = [m.start() for m in re.finditer(re.escape(marker), diff)]

    if not marker_indices:
        # No markers found, treat as single section
        return diff[:max_chars]

    # Define section boundaries
    # Section 0 starts at 0 and ends at the next marker
    # Section 1 starts at marker_indices[1] and ends at marker_indices[2]...
    # The last section ends at len(diff)

    section_starts = [0] + marker_indices[1:]
    section_ends = marker_indices[1:] + [len(diff)]

    # We need to keep track of which markers we are skipping to extract paths
    # Each marker_indices[i] corresponds to a section starting at that marker (or 0 for the first)
    # However, the marker itself is at marker_indices[i].

    # Let's rebuild sections and their corresponding 'b/' paths for omitted ones
    sections = []
    for start, end in zip(section_starts, section_ends):
        sections.append(diff[start:end])

    # Now find how many sections we can fit
    accumulated_diff = ""
    omitted_paths = []

    # We also need to know the paths of the skipped sections.
    # The skipped sections are those starting with a marker.
    # Let's find all paths first.
    all_paths = []
    for idx in marker_indices:
        match = re.search(r"diff --git a/\S+ b/(\S+)", diff[idx : idx + 100])
        if match:
            all_paths.append(match.group(1))

    # Iterate through sections and accumulate
    current_len = 0
    num_sections_included = 0

    # We'll check each section. Note: the first section includes the prefix.
    # If we include a section, we check if adding it exceeds max_chars.

    # Re-calculating section_starts and section_ends more carefully
    # If markers = [7, 30, 60], diff = "pre...diff--git...diff--git..."
    # sections = [diff[0:30], diff[30:60], diff[60:len(diff)]]
    # Section 0 starts at 0, ends at 30.
    # Section 1 starts at 30, ends at 60.
    # Section 2 starts at 60, ends at len(diff).

    # Let's use the sections we built.
    for i, section in enumerate(sections):
        # If we add this section, will it exceed max_chars?
        # We need to be careful about the newline before the footer.
        # The requirement says: "Return accumulated_diff + '\n\n' + footer (or just + footer if accumulated is empty)."
        # "The result of truncate_diff never exceeds max_chars + len(footer) in total length"

        # Let's check if adding this section exceeds max_chars.
        # We'll assume footer doesn't count towards max_chars.
        # Wait, "The result of truncate_diff never exceeds max_chars + len(footer) in total length"
        # This means (len(accumulated_diff) + len(footer)) <= max_chars + len(footer)
        # So len(accumulated_diff) <= max_chars.

        # However, if the first section alone is > max_chars, we truncate it to exactly max_chars.

        if i == 0 and len(section) > max_chars:
            # First section is already too big
            accumulated_diff = section[:max_chars]
            # We have omitted all other sections
            # The paths to omit are all paths from marker_indices[1:]
            # Wait, if first section is too big, we skip everything else.
            # But we still need to list the omitted paths.

            # Which paths are omitted? All paths from index 1 onwards.
            # marker_indices[0] is the first marker. Its path is NOT omitted.
            # marker_indices[1:] are the markers for omitted sections.
            omitted_paths = []
            for idx in marker_indices[1:]:
                match = re.search(r"diff --git a/\S+ b/(\S+)", diff[idx : idx + 100])
                if match:
                    omitted_paths.append(match.group(1))

            # Break and go to footer
            num_sections_included = 0  # No sections fully included
            break

        if current_len + len(section) <= max_chars:
            # If this is not the first section, we need a separator if it's not already there
            # But the sections we built are contiguous.
            # However, when we join them, we might need newlines.
            # Let's see. section 0 is diff[0:30], section 1 is diff[30:60].
            # They are contiguous. So we can just append.
            if not accumulated_diff:
                accumulated_diff = section
            else:
                # We don't need to add anything because they are contiguous.
                # UNLESS the section we are adding doesn't start with a newline.
                # But diffs usually have newlines.
                accumulated_diff += section

            current_len += len(section)
            num_sections_included += 1
        else:
            # This section would exceed max_chars.
            # We stop here. The current section and all subsequent are "omitted".
            # Wait, if we stop here, the current section is NOT included.
            # But what about its path? It's an omitted section.
            # We need to extract paths for all sections from this one onwards.

            # The current section starts at the start of the section we are trying to add.
            # That start index is either 0 (if i=0) or marker_indices[i] (if i>0).
            # Wait, if i=0 and len(section) > max_s, we already handled it.
            # So here i > 0. The start is marker_indices[i].

            # All paths from marker_indices[i] to the end are omitted.
            # But we must also check if the "current" section's path is part of the "omitted" count.
            # If we don't include it, it's omitted.

            # Let's collect paths for all indices from marker_indices[i] to the end.
            # Wait, marking the start of the *next* section.
            # The indices of markers that start omitted sections are:
            # marker_indices[i], marker_indices[i+1], ..., marker_indices[last]
            # But wait, if we are at index i, and we don't include section i,
            # then section i is omitted. Its start is marker_indices[i] or something?
            # No, if i > 0, the start of section i is marker_indices[i].

            # Let's refine:
            # marker_indices = [idx0, idx1, idx2, ...]
            # sections = [diff[0:idx1], diff[idx1:idx2], diff[idx2:len(diff)]]
            # if i=0 is too big, handled.
            # if i=1 is too big, we include section 0. Omitted: section 1, section 2...
            # The markers for omitted sections are idx1, idx2, ...

            # Let's find all markers that are NOT in the included sections.
            # If we included sections up to i-1, the markers in them are None (for section 0)
            # and marker_indices[1]...marker_indices[i-1].
            # The omitted markers are marker_indices[i], marker_indices[i+1]...

            for idx in marker_indices[i:]:
                match = re.search(r"diff --git a/\S+ b/(\S+)", diff[idx : idx + 100])
                if match:
                    omitted_paths.append(match.group(1))

            break

    # Final check: if we have omitted paths, we need the footer.
    # "Return accumulated_diff + '\n\n' + footer (or just + footer if accumulated is empty)."
    # "The result of truncate_diff never exceeds max_chars + len(footer) in total length"

    if not omitted_paths:
        return accumulated_diff

    footer = f"[... {len(omitted_paths)} more file(s) omitted: {', '.join(omitted_paths)} ...]"

    if not accumulated_diff:
        # This happens if the first section was truncated or if we couldn't even fit section 0.
        # But we handled the i=0 and len(section) > max_chars case above.
        return footer  # Should not happen based on logic.

    return f"{accumulated_diff}\n\n{footer}"
