# Marking site-specific information

*2026-11-22, Jeff's rule: site-specific facts may stay in the public repository, but they are
earmarked so they can be found and sanitized later.*

Site-specific means anything true only of one bench, place or unit: hamlet's hostnames,
addresses and paths; the bench's location, its neighbours and local noise sources; stations,
repeaters, towers and systems heard there (frequencies, callsigns, site IDs, levels); one
unit's serial number or measured crystal; dates and times of bench sessions tied to a place.
General findings (how a radio behaves, a method, a rule of thumb) are not.

Markdown, a block (paragraphs, lists, tables, whole sections):

    <!-- SITE-SPECIFIC: begin -->
    ...
    <!-- SITE-SPECIFIC: end -->

Keep a blank line on each side of the markers, so the block around them still renders.

Markdown, one line: `<!-- SITE-SPECIFIC -->` at its start. One table row: the same comment at
the start of its first cell, `| <!-- SITE-SPECIFIC --> serial | ... |` (a comment line between
rows would split the table).

Code, scripts and job files: a comment with the same words, `SITE-SPECIFIC:` and why, on the
line or above the block.

Find everything marked (other threads also write a bare `SITE-SPECIFIC` in headings, notes and PR bodies, which the same grep finds):

    grep -rn "SITE-SPECIFIC" docs tools tests

Docs written before 2026-11-22 are not marked yet; mark them when you next edit them.
