"""Disk-backed B+ tree index — the headline data-structures artifact.

A B+ tree is a balanced search tree tuned for disk. Each node is one 4KB page,
so the fan-out (keys per node) is large and the tree stays only 2-3 levels deep
for millions of keys. The cost that matters is the number of *page reads* per
operation, O(log_b n) with a large base b — not the comparisons.

Two properties make it a B+ tree (not a plain B-tree):

  * All data (key -> RowId) lives in the LEAVES. Internal nodes hold only
    separator keys that route searches, which keeps them dense and the tree
    shallow.
  * Leaves are LINKED in sorted order, so a range scan descends once to the
    first leaf and then walks the leaf chain — no per-key re-traversal. This is
    exactly what a hash index cannot do.

Insertion grows the tree at the ROOT: a full leaf splits in two and pushes a
separator up to its parent; if the parent overflows it splits too, recursively;
if the root splits, a new root is created and the tree gains a level. That is
how the tree stays balanced.

DESIGN CHOICES & SIMPLIFICATIONS (see DESIGN.md):
  * Nodes ride on the buffer pool: a node is loaded by parsing its page bytes
    into an in-memory _Node, mutated, then the whole node is re-serialized back
    into the page. Correctness-first; the production optimization is in-place
    byte edits to avoid full (de)serialization per touch.
  * Because a node is fully re-serialized on store(), the buffer pool's lack of
    pinning is NOT a hazard here: we never depend on a cached Page object
    surviving between load and store.
  * The root pointer and max_keys live in a META PAGE (page 1) so the tree
    survives a restart. Page 0 is the pager header; nodes use page 2 onward.
  * Integer keys only; DUPLICATE keys are allowed (a non-unique index maps one
    key to many RowIds). TEXT keys are future work (need comparable key bytes).
  * DELETE is leaf-only: the entry is removed from its leaf, but underfull
    nodes are NOT merged or rebalanced and separator keys are left in place.
    Search stays correct because separators need not exist as live data. The
    cost is space: a delete-heavy tree can become underfull and is not
    reclaimed. Production B+ trees rebalance (and Postgres/SQLite do).

Complexity: search / insert / delete are O(log_b n) page reads (b = fan-out,
hundreds). Range scan is O(log_b n + k/b) for k results — descend once, then
stream leaves. The split on insert is O(b) in-node work, amortized to O(1).
"""

from __future__ import annotations

import bisect
import struct
from typing import Iterator, Optional

from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import RowId
from queryx.storage.page import PAGE_SIZE

# -- node binary layouts -----------------------------------------------------
# Leaf:     [type=1 (B)][num_keys (H)][next_leaf page_no (I)] then num_keys ×
#           [key (q)][rowid.page_no (I)][rowid.slot (I)]
# Internal: [type=0 (B)][num_keys (H)] then num_keys × [key (q)]
#           then (num_keys + 1) × [child page_no (I)]
_LEAF, _INTERNAL = 1, 0
_LEAF_HEADER = struct.Struct("<BHI")   # 7 bytes
_LEAF_ENTRY = struct.Struct("<qII")    # 16 bytes
_INT_HEADER = struct.Struct("<BH")     # 3 bytes
_KEY = struct.Struct("<q")             # 8 bytes
_CHILD = struct.Struct("<I")           # 4 bytes

#: A node may transiently hold max_keys + 1 entries before it splits. Both node
#: types' transient size must fit in a page; the leaf is the tighter bound:
#:   _LEAF_HEADER + (max_keys + 1) * _LEAF_ENTRY <= PAGE_SIZE
#: which gives max_keys <= 254 for a 4096-byte page.
_MAX_KEYS_LIMIT = (PAGE_SIZE - _LEAF_HEADER.size) // _LEAF_ENTRY.size - 1
DEFAULT_MAX_KEYS = _MAX_KEYS_LIMIT  # 254

# -- meta page (page 1) ------------------------------------------------------
_META = struct.Struct("<4sHI")  # magic, max_keys, root page_no
_META_MAGIC = b"BPT1"
_META_PAGE = 1


class _Node:
    """In-memory view of one B+ tree node (leaf or internal)."""

    __slots__ = ("page_no", "is_leaf", "keys", "values", "children", "next_leaf")

    def __init__(
        self,
        page_no: int,
        is_leaf: bool,
        keys: list[int],
        values: Optional[list[RowId]] = None,
        children: Optional[list[int]] = None,
        next_leaf: int = 0,
    ) -> None:
        self.page_no = page_no
        self.is_leaf = is_leaf
        self.keys = keys
        self.values = values if values is not None else []      # leaf only
        self.children = children if children is not None else []  # internal only
        self.next_leaf = next_leaf  # leaf only; 0 = none


class BPlusTree:
    """A disk-backed B+ tree mapping integer keys to RowIds (duplicates allowed)."""

    def __init__(self, pool: BufferPool, max_keys: Optional[int] = None) -> None:
        self._pool = pool
        pager = pool.pager
        if pager.num_pages <= 1:
            # Fresh file (only the pager header exists): build meta + empty root.
            mk = DEFAULT_MAX_KEYS if max_keys is None else max_keys
            if not 2 <= mk <= _MAX_KEYS_LIMIT:
                raise ValueError(f"max_keys must be in [2, {_MAX_KEYS_LIMIT}], got {mk}")
            self._max_keys = mk
            meta_no, _ = pool.new_page()  # page 1
            assert meta_no == _META_PAGE, "meta page must be page 1"
            root_no, _ = pool.new_page()  # page 2
            self._root = root_no
            self._store(_Node(root_no, is_leaf=True, keys=[]))
            self._write_meta()
        else:
            self._read_meta()

    # -- meta page ----------------------------------------------------------

    def _write_meta(self) -> None:
        buf = bytearray(PAGE_SIZE)
        _META.pack_into(buf, 0, _META_MAGIC, self._max_keys, self._root)
        page = self._pool.get_page(_META_PAGE)
        page.overwrite(bytes(buf))
        self._pool.mark_dirty(_META_PAGE)

    def _read_meta(self) -> None:
        raw = self._pool.get_page(_META_PAGE).to_bytes()
        magic, max_keys, root = _META.unpack_from(raw, 0)
        if magic != _META_MAGIC:
            raise ValueError(f"not a B+ tree index (bad meta magic {magic!r})")
        self._max_keys = max_keys
        self._root = root

    # -- node serialization -------------------------------------------------

    def _load(self, page_no: int) -> _Node:
        raw = self._pool.get_page(page_no).to_bytes()
        if raw[0] == _LEAF:
            _, num, next_leaf = _LEAF_HEADER.unpack_from(raw, 0)
            keys: list[int] = []
            values: list[RowId] = []
            pos = _LEAF_HEADER.size
            for _ in range(num):
                key, pno, slot = _LEAF_ENTRY.unpack_from(raw, pos)
                keys.append(key)
                values.append(RowId(pno, slot))
                pos += _LEAF_ENTRY.size
            return _Node(page_no, True, keys, values=values, next_leaf=next_leaf)
        # internal
        _, num = _INT_HEADER.unpack_from(raw, 0)
        keys = []
        pos = _INT_HEADER.size
        for _ in range(num):
            (key,) = _KEY.unpack_from(raw, pos)
            keys.append(key)
            pos += _KEY.size
        children: list[int] = []
        for _ in range(num + 1):
            (child,) = _CHILD.unpack_from(raw, pos)
            children.append(child)
            pos += _CHILD.size
        return _Node(page_no, False, keys, children=children)

    def _store(self, node: _Node) -> None:
        buf = bytearray(PAGE_SIZE)
        if node.is_leaf:
            _LEAF_HEADER.pack_into(buf, 0, _LEAF, len(node.keys), node.next_leaf)
            pos = _LEAF_HEADER.size
            for key, rid in zip(node.keys, node.values):
                _LEAF_ENTRY.pack_into(buf, pos, key, rid.page_no, rid.slot)
                pos += _LEAF_ENTRY.size
        else:
            _INT_HEADER.pack_into(buf, 0, _INTERNAL, len(node.keys))
            pos = _INT_HEADER.size
            for key in node.keys:
                _KEY.pack_into(buf, pos, key)
                pos += _KEY.size
            for child in node.children:
                _CHILD.pack_into(buf, pos, child)
                pos += _CHILD.size
        page = self._pool.get_page(node.page_no)
        page.overwrite(bytes(buf))
        self._pool.mark_dirty(node.page_no)

    # -- search -------------------------------------------------------------

    def _find_leaf(self, key: int) -> _Node:
        """Descend to the leftmost leaf that could contain ``key``.

        Uses bisect_left at internal nodes so that, with duplicate keys, we land
        at or before the first leaf holding ``key``; callers then walk right
        along the leaf chain to collect all matches.
        """
        node = self._load(self._root)
        while not node.is_leaf:
            child_index = bisect.bisect_left(node.keys, key)
            node = self._load(node.children[child_index])
        return node

    def search(self, key: int) -> list[RowId]:
        """Return every RowId stored under ``key`` (empty list if none)."""
        node = self._find_leaf(key)
        results: list[RowId] = []
        while node is not None:
            i = bisect.bisect_left(node.keys, key)
            while i < len(node.keys) and node.keys[i] == key:
                results.append(node.values[i])
                i += 1
            if i < len(node.keys):
                break  # hit a key > target: no more matches anywhere
            # Exhausted this leaf at its end. The leftmost-leaf descent can land
            # at or before the target leaf, so keep walking right while this
            # leaf's max key is <= target (target not yet passed).
            if node.next_leaf and node.keys and node.keys[-1] <= key:
                node = self._load(node.next_leaf)
            else:
                break
        return results

    def range_scan(
        self, low: Optional[int] = None, high: Optional[int] = None
    ) -> Iterator[tuple[int, RowId]]:
        """Yield (key, RowId) for low <= key <= high, in ascending key order.

        ``low``/``high`` of None mean unbounded. Descends once to the start leaf,
        then streams the linked leaf chain — the B+ tree's signature operation.
        """
        if low is None:
            node: Optional[_Node] = self._leftmost_leaf()
            i = 0
        else:
            node = self._find_leaf(low)
            i = bisect.bisect_left(node.keys, low)
        while node is not None:
            while i < len(node.keys):
                key = node.keys[i]
                if high is not None and key > high:
                    return
                yield key, node.values[i]
                i += 1
            node = self._load(node.next_leaf) if node.next_leaf else None
            i = 0

    def _leftmost_leaf(self) -> _Node:
        node = self._load(self._root)
        while not node.is_leaf:
            node = self._load(node.children[0])
        return node

    # -- insert -------------------------------------------------------------

    def insert(self, key: int, rowid: RowId) -> None:
        """Insert (key, rowid); splits propagate up and may grow a new root."""
        root = self._load(self._root)
        split = self._insert_into(root, key, rowid)
        if split is not None:
            sep_key, right_no = split
            new_root_no, _ = self._pool.new_page()
            self._store(
                _Node(new_root_no, is_leaf=False, keys=[sep_key],
                      children=[self._root, right_no])
            )
            self._root = new_root_no
            self._write_meta()

    def _insert_into(self, node: _Node, key: int, rowid: RowId) -> Optional[tuple[int, int]]:
        """Insert into the subtree at ``node``. Return (sep_key, new_right_page)
        if ``node`` split, else None."""
        if node.is_leaf:
            pos = bisect.bisect_right(node.keys, key)  # append after equal keys
            node.keys.insert(pos, key)
            node.values.insert(pos, rowid)
            if len(node.keys) <= self._max_keys:
                self._store(node)
                return None
            return self._split_leaf(node)

        child_index = bisect.bisect_right(node.keys, key)
        child = self._load(node.children[child_index])
        split = self._insert_into(child, key, rowid)
        if split is None:
            return None
        sep_key, right_no = split
        node.keys.insert(child_index, sep_key)
        node.children.insert(child_index + 1, right_no)
        if len(node.keys) <= self._max_keys:
            self._store(node)
            return None
        return self._split_internal(node)

    def _split_leaf(self, node: _Node) -> tuple[int, int]:
        mid = len(node.keys) // 2
        right_no, _ = self._pool.new_page()
        right = _Node(
            right_no, is_leaf=True,
            keys=node.keys[mid:], values=node.values[mid:],
            next_leaf=node.next_leaf,
        )
        node.keys = node.keys[:mid]
        node.values = node.values[:mid]
        node.next_leaf = right_no
        self._store(node)
        self._store(right)
        return right.keys[0], right_no  # separator = smallest key in right leaf

    def _split_internal(self, node: _Node) -> tuple[int, int]:
        mid = len(node.keys) // 2
        sep_key = node.keys[mid]  # middle key is promoted, not copied
        right_no, _ = self._pool.new_page()
        right = _Node(
            right_no, is_leaf=False,
            keys=node.keys[mid + 1:], children=node.children[mid + 1:],
        )
        node.keys = node.keys[:mid]
        node.children = node.children[:mid + 1]
        self._store(node)
        self._store(right)
        return sep_key, right_no

    # -- delete (leaf-only, no rebalancing) ---------------------------------

    def delete(self, key: int, rowid: RowId) -> bool:
        """Remove the specific (key, rowid) entry. Returns False if not found.

        Leaf-only: the entry is dropped from its leaf; underfull nodes are not
        merged and separators are left in place (search stays correct). See the
        module docstring for why this is a deliberate simplification.
        """
        node: Optional[_Node] = self._find_leaf(key)
        while node is not None:
            i = bisect.bisect_left(node.keys, key)
            while i < len(node.keys) and node.keys[i] == key:
                if node.values[i] == rowid:
                    del node.keys[i]
                    del node.values[i]
                    self._store(node)
                    return True
                i += 1
            if i < len(node.keys):
                return False  # passed a key > target without a match
            if node.next_leaf and node.keys and node.keys[-1] <= key:
                node = self._load(node.next_leaf)
            else:
                return False
        return False

    # -- introspection / lifecycle -----------------------------------------

    @property
    def root_page(self) -> int:
        return self._root

    @property
    def max_keys(self) -> int:
        return self._max_keys

    def height(self) -> int:
        """Number of levels (1 = a single leaf root). Useful for tests/EXPLAIN."""
        levels = 1
        node = self._load(self._root)
        while not node.is_leaf:
            levels += 1
            node = self._load(node.children[0])
        return levels

    def flush(self) -> None:
        """Persist all buffered node/meta changes through the pager."""
        self._pool.flush_all()
