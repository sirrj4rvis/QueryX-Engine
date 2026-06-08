"""Phase 2, slice 1 — slotted page and row serialization tests.

These prove the slotted-page invariants the rest of the storage engine relies
on: records survive a byte-level round trip, slot numbers are stable identities,
deletion frees a slot for reuse, and a full page refuses gracefully.
"""

import struct

import pytest

from queryx.storage.page import (
    HEADER_SIZE,
    PAGE_SIZE,
    SLOT_SIZE,
    ColumnType,
    Page,
    PageFullError,
    deserialize_row,
    serialize_row,
)


# ---------------------------------------------------------------------------
# Empty page
# ---------------------------------------------------------------------------


def test_empty_page_geometry():
    page = Page.empty()
    assert page.num_slots == 0
    # All space except the header is free.
    assert page.free_space() == PAGE_SIZE - HEADER_SIZE
    assert page.to_bytes() != b""
    assert len(page.to_bytes()) == PAGE_SIZE


def test_empty_page_has_no_records():
    page = Page.empty()
    assert list(page.records()) == []


# ---------------------------------------------------------------------------
# Insert / get
# ---------------------------------------------------------------------------


def test_insert_returns_sequential_slot_indices():
    page = Page.empty()
    assert page.insert_record(b"alpha") == 0
    assert page.insert_record(b"beta") == 1
    assert page.insert_record(b"gamma") == 2
    assert page.num_slots == 3


def test_get_returns_exact_bytes():
    page = Page.empty()
    i = page.insert_record(b"hello world")
    assert page.get_record(i) == b"hello world"


def test_records_grow_downward_and_coexist():
    page = Page.empty()
    payloads = [b"one", b"two", b"three", b"four"]
    slots = [page.insert_record(p) for p in payloads]
    # Every record reads back correctly regardless of insertion order/position.
    for slot, payload in zip(slots, payloads):
        assert page.get_record(slot) == payload


def test_free_space_decreases_by_record_plus_slot():
    page = Page.empty()
    before = page.free_space()
    page.insert_record(b"1234567890")  # 10 bytes
    # one new slot (SLOT_SIZE) + 10 record bytes consumed
    assert page.free_space() == before - (10 + SLOT_SIZE)


def test_empty_record_rejected():
    page = Page.empty()
    with pytest.raises(ValueError):
        page.insert_record(b"")


def test_get_out_of_range_raises():
    page = Page.empty()
    page.insert_record(b"x")
    with pytest.raises(IndexError):
        page.get_record(5)


# ---------------------------------------------------------------------------
# Delete / reuse
# ---------------------------------------------------------------------------


def test_delete_marks_slot_dead():
    page = Page.empty()
    i = page.insert_record(b"doomed")
    assert page.delete_record(i) is True
    assert page.get_record(i) is None


def test_delete_twice_is_noop():
    page = Page.empty()
    i = page.insert_record(b"doomed")
    page.delete_record(i)
    assert page.delete_record(i) is False


def test_deleted_record_skipped_in_iteration():
    page = Page.empty()
    a = page.insert_record(b"keep-a")
    b = page.insert_record(b"drop-b")
    c = page.insert_record(b"keep-c")
    page.delete_record(b)
    live = dict(page.records())
    assert live == {a: b"keep-a", c: b"keep-c"}


def test_insert_reuses_dead_slot_without_growing_slot_array():
    page = Page.empty()
    a = page.insert_record(b"first")
    page.insert_record(b"second")
    page.delete_record(a)
    slots_before = page.num_slots
    reused = page.insert_record(b"third")
    assert reused == a  # the dead slot index was reused
    assert page.num_slots == slots_before  # no new slot entry appended
    assert page.get_record(a) == b"third"


# ---------------------------------------------------------------------------
# Page full
# ---------------------------------------------------------------------------


def test_page_full_raises():
    page = Page.empty()
    big = b"x" * (PAGE_SIZE - HEADER_SIZE - SLOT_SIZE)  # exactly fills the page
    page.insert_record(big)
    with pytest.raises(PageFullError):
        page.insert_record(b"no room")


def test_largest_exact_fit_succeeds():
    page = Page.empty()
    big = b"x" * (PAGE_SIZE - HEADER_SIZE - SLOT_SIZE)
    i = page.insert_record(big)
    assert page.get_record(i) == big
    assert page.free_space() == 0


# ---------------------------------------------------------------------------
# Disk round trip
# ---------------------------------------------------------------------------


def test_page_survives_bytes_round_trip():
    page = Page.empty()
    page.insert_record(b"persist-me")
    page.insert_record(b"and-me")
    raw = page.to_bytes()
    assert len(raw) == PAGE_SIZE

    restored = Page.from_bytes(raw)
    assert restored.get_record(0) == b"persist-me"
    assert restored.get_record(1) == b"and-me"
    assert restored.num_slots == 2


def test_from_bytes_rejects_wrong_size():
    with pytest.raises(ValueError):
        Page.from_bytes(b"too short")


# ---------------------------------------------------------------------------
# Row serialization
# ---------------------------------------------------------------------------


def test_serialize_deserialize_round_trip():
    schema = [ColumnType.INT, ColumnType.TEXT, ColumnType.INT]
    row = (1, "alice", 30)
    data = serialize_row(schema, row)
    assert deserialize_row(schema, data) == row


def test_serialize_handles_negative_and_large_ints():
    schema = [ColumnType.INT]
    for value in (-1, 0, 2**40, -(2**40)):
        assert deserialize_row(schema, serialize_row(schema, (value,))) == (value,)


def test_serialize_handles_unicode_text():
    schema = [ColumnType.TEXT]
    row = ("héllo — 世界",)
    assert deserialize_row(schema, serialize_row(schema, row)) == row


def test_serialize_empty_string():
    schema = [ColumnType.TEXT]
    assert deserialize_row(schema, serialize_row(schema, ("",))) == ("",)


def test_serialize_rejects_arity_mismatch():
    with pytest.raises(ValueError):
        serialize_row([ColumnType.INT, ColumnType.TEXT], (1,))


def test_serialized_row_stores_and_loads_through_a_page():
    """The end-to-end path: typed row -> bytes -> page -> bytes -> typed row."""
    schema = [ColumnType.INT, ColumnType.TEXT]
    page = Page.empty()
    slot = page.insert_record(serialize_row(schema, (7, "seven")))
    raw = page.to_bytes()

    restored = Page.from_bytes(raw)
    record = restored.get_record(slot)
    assert record is not None
    assert deserialize_row(schema, record) == (7, "seven")


def test_int_serialization_is_eight_bytes():
    # Guards the on-disk format so a future change can't silently break old files.
    assert serialize_row([ColumnType.INT], (1,)) == struct.pack("<q", 1)
