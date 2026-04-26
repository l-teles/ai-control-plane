"""DAG construction and traversal for Claude transcript entries.

A Claude JSONL file is a linear append log, but the messages it contains
form a directed acyclic graph linked by ``parentUuid``.  When the user
resumes a session from a non-leaf point — or when subagent transcripts
get interleaved into the main file — the file order no longer matches
the conversation order.

This module rebuilds the DAG from ``parent_uuid`` and walks it
depth-first, so each child appears immediately after its parent
regardless of where it was written in the file.
"""

from __future__ import annotations

from collections import defaultdict

from .models import TranscriptEntry

# Public so callers can clamp very deep recursion explicitly if needed.
MAX_DEPTH = 50_000


def order_by_dag(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """Reorder *entries* so each child follows its parent.

    Sibling entries (multiple children of the same parent, or multiple
    roots) are sorted by ``(timestamp, file_index)`` — earlier timestamp
    first, file-write order as deterministic tiebreak.  This puts the
    older branch ahead of a resumed branch that diverges from the same
    parent, which matches what users expect when reading a session that
    was resumed mid-thread.

    Entries whose ``parent_uuid`` points to an entry not in the file (an
    "orphan", typically from a session resume) are treated as roots and
    sorted with the same key alongside any genuine root.

    Entries without a ``uuid`` are passed through untouched in their
    original positions — they're metadata events (``summary``, hooks,
    progress, file-history snapshots) that don't belong in the DAG.
    """
    by_uuid: dict[str, TranscriptEntry] = {}
    file_index: dict[str, int] = {}
    for i, e in enumerate(entries):
        if e.uuid and e.uuid not in by_uuid:
            by_uuid[e.uuid] = e
            file_index[e.uuid] = i

    children_of: dict[str, list[TranscriptEntry]] = defaultdict(list)
    roots: list[TranscriptEntry] = []
    no_uuid: list[tuple[int, TranscriptEntry]] = []

    for i, e in enumerate(entries):
        if not e.uuid:
            no_uuid.append((i, e))
            continue
        # Treat as a child only if the parent is a different entry (avoid
        # self-cycles and duplicate-uuid edges).
        parent_entry = by_uuid.get(e.parent_uuid) if e.parent_uuid else None
        if parent_entry is not None and parent_entry is not e:
            children_of[e.parent_uuid].append(e)
        else:
            roots.append(e)

    def _sort_key(e: TranscriptEntry) -> tuple[str, int]:
        return (e.timestamp or "", file_index.get(e.uuid, 0))

    for kids in children_of.values():
        kids.sort(key=_sort_key)
    roots.sort(key=_sort_key)

    out: list[TranscriptEntry] = []
    visited: set[int] = set()  # set of id(entry) — identity-based to handle duplicate UUIDs

    def _walk(start: TranscriptEntry) -> None:
        # Iterative DFS to avoid recursion-depth issues on very long sessions.
        stack: list[TranscriptEntry] = [start]
        while stack:
            node = stack.pop()
            if id(node) in visited:
                continue
            visited.add(id(node))
            out.append(node)
            # Push reversed so the leftmost child is processed first.
            stack.extend(reversed(children_of.get(node.uuid, [])))

    for root in roots:
        _walk(root)

    # Handle cycles or fully orphaned subgraphs: any entry whose ancestor
    # chain forms a loop won't have been reached above, since neither it
    # nor its parents are in `roots`. Treat the first unvisited entry of
    # each remaining subgraph as a root and walk it.
    for e in entries:
        if e.uuid and id(e) not in visited:
            _walk(e)

    # Re-insert UUID-less events at their original file positions, by
    # walking the output and slotting them in based on the position of
    # their nearest preceding UUID-bearing event.
    if no_uuid:
        # Map original-file index of each UUID-bearing entry -> position in `out`.
        # Use the *first* file index for each uuid (duplicates resolve to first).
        out_pos_by_index: dict[int, int] = {}
        for pos, e in enumerate(out):
            idx = file_index.get(e.uuid)
            if idx is not None and idx not in out_pos_by_index:
                out_pos_by_index[idx] = pos

        # Walk no_uuid events in file order, splicing each one in just
        # after the preceding UUID-bearing event in `out`.
        offset = 0
        for orig_idx, e in no_uuid:
            preceding = max(
                (pos for idx, pos in out_pos_by_index.items() if idx < orig_idx),
                default=-1,
            )
            insert_at = preceding + 1 + offset
            out.insert(insert_at, e)
            offset += 1

    return out


def find_sidechain_runs(entries: list[TranscriptEntry]) -> list[list[TranscriptEntry]]:
    """Group consecutive sidechain entries into "runs" sharing a parent chain.

    Each run represents the internal transcript of a single subagent
    invocation.  A run starts at a sidechain entry whose direct parent is
    *not* a sidechain (or is missing from the file), and contains every
    sidechain descendant reachable from that root.

    The runs are returned in the order their roots first appear in the
    input list.  Within each run, entries are yielded in DAG-order
    (assumes *entries* has been processed by :func:`order_by_dag`).
    """
    by_uuid: dict[str, TranscriptEntry] = {e.uuid: e for e in entries if e.uuid}
    children_of: dict[str, list[TranscriptEntry]] = defaultdict(list)
    for e in entries:
        if e.uuid and e.parent_uuid and e.parent_uuid in by_uuid:
            children_of[e.parent_uuid].append(e)

    def _is_run_root(e: TranscriptEntry) -> bool:
        if not e.is_sidechain:
            return False
        parent = by_uuid.get(e.parent_uuid)
        return parent is None or not parent.is_sidechain

    runs: list[list[TranscriptEntry]] = []
    for e in entries:
        if not _is_run_root(e):
            continue
        run: list[TranscriptEntry] = []
        stack: list[TranscriptEntry] = [e]
        # Identity-based dedup so a sidechain that happens to reuse a UUID
        # (test fixtures, malformed input) doesn't silently drop later
        # entries — same approach as ``order_by_dag`` for consistency.
        seen: set[int] = set()
        while stack:
            node = stack.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if node.is_sidechain:
                run.append(node)
                stack.extend(reversed(children_of.get(node.uuid, [])))
        # The DFS above produces DAG-order; the input is already DAG-ordered
        # by ``order_by_dag`` per the docstring contract, so we don't add a
        # timestamp sort that would risk re-ordering siblings whose
        # timestamps tie or — in malformed input — go backwards.
        runs.append(run)
    return runs
