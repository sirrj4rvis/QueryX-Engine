"""Phase 1 smoke tests.

These do not test engine behavior (there is none yet). They prove the skeleton
is wired correctly: every module imports without error, the package version is
present, and the by-layer structure exists as designed. A green suite here means
the scaffolding is sound and later phases have a place to slot into.
"""

import importlib

import pytest

import queryx


# Every module the architecture calls for, by layer (top to bottom).
ALL_MODULES = [
    "queryx",
    "queryx.database",
    "queryx.catalog",
    "queryx.sql",
    "queryx.sql.tokens",
    "queryx.sql.lexer",
    "queryx.sql.ast",
    "queryx.sql.parser",
    "queryx.execution",
    "queryx.execution.operators",
    "queryx.planner",
    "queryx.planner.statistics",
    "queryx.planner.optimizer",
    "queryx.planner.explain",
    "queryx.index",
    "queryx.index.btree",
    "queryx.index.hash_index",
    "queryx.storage",
    "queryx.storage.page",
    "queryx.storage.pager",
    "queryx.storage.buffer_pool",
    "queryx.storage.heap_file",
    "queryx.wal",
    "queryx.wal.log",
    "queryx.wal.recovery",
]


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_imports(module_name):
    """Each planned module exists and imports cleanly."""
    module = importlib.import_module(module_name)
    assert module is not None


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_is_documented(module_name):
    """Each module carries a docstring explaining its responsibility."""
    module = importlib.import_module(module_name)
    assert module.__doc__ and module.__doc__.strip(), (
        f"{module_name} is missing its responsibility docstring"
    )


def test_version_present():
    assert queryx.__version__ == "0.1.0"
