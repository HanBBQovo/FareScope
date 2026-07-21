import re
from pathlib import Path

from app import models
from app.db.base import Base
from performance.contracts import (
    CRITICAL_INDEXES,
    DEFAULT_PAGE_SIZE,
    HOT_QUERY_CONTRACTS,
    MAX_PAGE_SIZE,
    PARTITIONED_TABLES,
)

SERVER_ROOT = Path(__file__).resolve().parents[2]
EXPLAIN_SQL = SERVER_ROOT / "performance" / "explain_hot_queries.sql"
GENERATOR_SQL = SERVER_ROOT / "performance" / "generate_load.sql"
HOT_QUERY_PATTERN = re.compile(
    r"-- hot-query: (?P<name>[a-z0-9-]+)\n(?P<body>.*?)-- end-hot-query",
    re.DOTALL,
)


def _declared_indexes(table_name: str) -> dict[str, tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
        if index.name is not None
    }


def test_models_are_loaded_for_contract_inspection() -> None:
    assert models.__all__


def test_minimum_hot_path_indexes_are_declared() -> None:
    for table_name, expected_indexes in CRITICAL_INDEXES.items():
        declared = _declared_indexes(table_name)
        for index_name, expected_columns in expected_indexes.items():
            assert declared.get(index_name) == expected_columns


def test_observation_partition_contract_is_declared() -> None:
    for table_name, partition_column in PARTITIONED_TABLES.items():
        table = Base.metadata.tables[table_name]
        partition_clause = table.dialect_options["postgresql"]["partition_by"]
        assert partition_clause == f"RANGE ({partition_column})"


def test_hot_queries_are_keyset_bounded() -> None:
    query_blocks = {
        match.group("name"): match.group("body")
        for match in HOT_QUERY_PATTERN.finditer(EXPLAIN_SQL.read_text())
    }
    expected_names = {contract.name for contract in HOT_QUERY_CONTRACTS}
    assert set(query_blocks) == expected_names

    for contract in HOT_QUERY_CONTRACTS:
        normalized = " ".join(query_blocks[contract.name].upper().split())
        assert "EXPLAIN (ANALYZE" in normalized
        assert "ORDER BY" in normalized
        assert "LIMIT :" in normalized
        assert " OFFSET " not in f" {normalized} "
        for column in contract.keyset_columns:
            assert column.upper() in normalized


def test_page_size_contract_has_a_hard_ceiling() -> None:
    assert 1 <= DEFAULT_PAGE_SIZE <= MAX_PAGE_SIZE <= 100
    assert all(contract.maximum_rows <= MAX_PAGE_SIZE for contract in HOT_QUERY_CONTRACTS)


def test_load_generator_requires_disposable_database_confirmation() -> None:
    generator = GENERATOR_SQL.read_text()
    assert "I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE" in generator
    assert "TRUNCATE" not in generator.upper()
    assert "ANALYZE price_observations" in generator
