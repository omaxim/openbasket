"""Kedro datasets for tables too large for pandas.

N-PORT holds millions of rows per quarter. Nodes that work on it pass
:class:`duckdb.DuckDBPyRelation` objects, and this dataset persists them as
Parquet, so the data stays inside DuckDB and Kedro still tracks lineage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
from kedro.io import AbstractDataset

_CONNECTION: duckdb.DuckDBPyConnection | None = None


def connection() -> duckdb.DuckDBPyConnection:
    """Return the DuckDB connection shared by all nodes in a run."""
    global _CONNECTION
    if _CONNECTION is None:
        _CONNECTION = duckdb.connect()
    return _CONNECTION


class DuckDBParquetDataset(AbstractDataset[duckdb.DuckDBPyRelation, duckdb.DuckDBPyRelation]):
    """Save a DuckDB relation to Parquet and load it back as a relation.

    Example catalog entry::

        nport_holdings:
          type: openbasket.datasets.DuckDBParquetDataset
          filepath: data/03_primary/nport_holdings.parquet
          order_by: SERIES_ID
    """

    def __init__(self, filepath: str, order_by: str | None = None, metadata: dict[str, Any] | None = None):
        self._filepath = Path(filepath)
        self._order_by = order_by
        self.metadata = metadata

    def load(self) -> duckdb.DuckDBPyRelation:
        return connection().read_parquet(str(self._filepath))

    def save(self, data: duckdb.DuckDBPyRelation) -> None:
        self._filepath.parent.mkdir(parents=True, exist_ok=True)
        if self._order_by:
            data = data.order(self._order_by)
        tmp = self._filepath.with_suffix(".tmp.parquet")
        data.write_parquet(str(tmp), compression="zstd")
        tmp.replace(self._filepath)

    def _exists(self) -> bool:
        return self._filepath.exists()

    def _describe(self) -> dict[str, Any]:
        return {"filepath": str(self._filepath), "order_by": self._order_by}
