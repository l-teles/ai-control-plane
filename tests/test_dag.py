"""Tests for DAG construction and ordering."""

from __future__ import annotations

from ai_ctrl_plane.dag import find_sidechain_runs, order_by_dag
from ai_ctrl_plane.models import TranscriptEntry


def _entry(uuid: str, parent: str = "", *, timestamp: str = "", is_sidechain: bool = False) -> TranscriptEntry:
    return TranscriptEntry(uuid=uuid, parent_uuid=parent, timestamp=timestamp, is_sidechain=is_sidechain)


def test_linear_chain_preserves_order() -> None:
    a = _entry("a", timestamp="t1")
    b = _entry("b", "a", timestamp="t2")
    c = _entry("c", "b", timestamp="t3")
    out = order_by_dag([a, b, c])
    assert [e.uuid for e in out] == ["a", "b", "c"]


def test_resumed_branch_visits_each_branch_in_full() -> None:
    """A=>B=>C=>D in the original thread, then user resumes from B and adds
    E=>F. Entries in file are [A, B, C, D, E, F].

    DAG-ordered output should keep A=>B together and visit one branch fully
    before the other. Within a parent's children, timestamp order rules.
    """
    a = _entry("a", timestamp="t01")
    b = _entry("b", "a", timestamp="t02")
    c = _entry("c", "b", timestamp="t03")  # original branch
    d = _entry("d", "c", timestamp="t04")
    e = _entry("e", "b", timestamp="t05")  # resumed branch
    f = _entry("f", "e", timestamp="t06")

    out = order_by_dag([a, b, c, d, e, f])
    uuids = [x.uuid for x in out]
    assert uuids[:2] == ["a", "b"]
    # Both branches present, in full, with their internal order intact
    c_idx, d_idx, e_idx, f_idx = (uuids.index(u) for u in "cdef")
    assert c_idx < d_idx and e_idx < f_idx
    # The earlier-timestamp branch (c/d) comes first because we sort
    # children of `b` by timestamp.
    assert c_idx < e_idx


def test_orphan_with_unknown_parent_is_treated_as_root() -> None:
    """When parent_uuid points to an entry not in the file, treat as root."""
    a = _entry("a", "missing-parent", timestamp="t1")
    b = _entry("b", "a", timestamp="t2")
    out = order_by_dag([a, b])
    assert [x.uuid for x in out] == ["a", "b"]


def test_entries_without_uuid_keep_relative_position() -> None:
    """Summary / file-history entries usually have no uuid — they should pass
    through in their original file order rather than be dropped."""
    a = _entry("a", timestamp="t1")
    summary = TranscriptEntry(type="summary", summary="x")
    b = _entry("b", "a", timestamp="t2")
    out = order_by_dag([a, summary, b])
    types = [(x.type, x.uuid) for x in out]
    assert types == [("", "a"), ("summary", ""), ("", "b")]


def test_cycle_protection() -> None:
    """A malformed file with parent_uuid forming a cycle shouldn't loop."""
    a = _entry("a", "b", timestamp="t1")
    b = _entry("b", "a", timestamp="t2")
    out = order_by_dag([a, b])
    # Both should appear exactly once and the function should return.
    assert sorted(x.uuid for x in out) == ["a", "b"]


def test_deterministic_tiebreak_uses_file_index() -> None:
    """When two children share a parent and a timestamp, the one written
    first in the file should come first."""
    a = _entry("a", timestamp="t1")
    b = _entry("b", "a", timestamp="t2")
    c = _entry("c", "a", timestamp="t2")
    out = order_by_dag([a, b, c])
    assert [x.uuid for x in out] == ["a", "b", "c"]
    out2 = order_by_dag([a, c, b])
    assert [x.uuid for x in out2] == ["a", "c", "b"]


def test_sidechain_run_collected_under_root() -> None:
    """A subagent thread (entries with is_sidechain=True) should be grouped
    into a single run, rooted at the first sidechain whose parent is on
    the main thread."""
    main = _entry("M", timestamp="t1")
    s1 = _entry("S1", "M", timestamp="t2", is_sidechain=True)
    s2 = _entry("S2", "S1", timestamp="t3", is_sidechain=True)
    s3 = _entry("S3", "S2", timestamp="t4", is_sidechain=True)
    after = _entry("A", "M", timestamp="t5")

    ordered = order_by_dag([main, s1, s2, s3, after])
    runs = find_sidechain_runs(ordered)
    assert len(runs) == 1
    assert [e.uuid for e in runs[0]] == ["S1", "S2", "S3"]


def test_two_subagent_runs_are_separated() -> None:
    """Two distinct subagent invocations (each rooted at the main thread)
    should yield two runs, each containing only its own messages."""
    m = _entry("M", timestamp="t1")
    a1 = _entry("A1", "M", timestamp="t2", is_sidechain=True)
    a2 = _entry("A2", "A1", timestamp="t3", is_sidechain=True)
    b1 = _entry("B1", "M", timestamp="t4", is_sidechain=True)
    b2 = _entry("B2", "B1", timestamp="t5", is_sidechain=True)

    ordered = order_by_dag([m, a1, a2, b1, b2])
    runs = find_sidechain_runs(ordered)
    assert len(runs) == 2
    assert {e.uuid for e in runs[0]} == {"A1", "A2"}
    assert {e.uuid for e in runs[1]} == {"B1", "B2"}


def test_find_sidechain_runs_handles_duplicate_uuids() -> None:
    """``find_sidechain_runs`` must use identity-based dedup, same as
    ``order_by_dag`` — a sidechain that reuses a UUID across distinct
    entries (e.g. test fixtures) would otherwise drop later entries
    silently. Regression for PR #27 review #24."""
    main = _entry("M", timestamp="t1")
    # Two distinct entries that happen to share the same uuid "S".
    s_first = _entry("S", "M", timestamp="t2", is_sidechain=True)
    s_second = _entry("S", "S", timestamp="t3", is_sidechain=True)
    # No call to order_by_dag — we want to test the dedup logic
    # directly without order_by_dag's identity-based pass interfering.
    runs = find_sidechain_runs([main, s_first, s_second])
    assert len(runs) == 1
    # Both entries appear in the run despite the shared uuid.
    assert len(runs[0]) == 2
