import pandas as pd
import pytest
from kedro.io import DataCatalog, MemoryDataset
from kedro.pipeline import Node, Pipeline
from kedro.runner import SequentialRunner

from openbasket import resilience


def _boom(x):
    raise ConnectionResetError(54, "Connection reset by peer")


def _run(pipeline, catalog):
    resilience._CATALOG = catalog
    SequentialRunner().run(pipeline, catalog)


def test_a_failed_optional_node_uses_the_last_saved_copy():
    old = pd.DataFrame({"isin": ["LU0274208692"]})
    catalog = DataCatalog({"x": MemoryDataset(1), "esma": MemoryDataset(old), "out": MemoryDataset()})
    pipe = resilience.soften_pipeline(Pipeline([
        Node(_boom, "x", "esma", name="fetch"),
        Node(lambda df: len(df), "esma", "out", name="use"),
    ]), {"fetch": None})
    _run(pipe, catalog)
    assert catalog.load("out") == 1
    assert resilience.FALLBACKS["fetch"] == "last saved copy"


def test_no_saved_copy_uses_empty_or_fails_as_before():
    catalog = DataCatalog({"x": MemoryDataset(1), "out": MemoryDataset()})
    soft = resilience.soften_pipeline(Pipeline([Node(_boom, "x", "esma", name="fetch"),
                                                Node(lambda df: len(df), "esma", "out", name="use")]),
                                      {"fetch": lambda: pd.DataFrame(columns=["isin"])})
    _run(soft, catalog)
    assert catalog.load("out") == 0
    hard = resilience.soften_pipeline(Pipeline([Node(_boom, "x", "y", name="fetch")]), {"other": None})
    with pytest.raises(ConnectionResetError):
        _run(hard, DataCatalog({"x": MemoryDataset(1)}))
