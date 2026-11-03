"""Project pipelines."""
from __future__ import annotations

from kedro.framework.project import find_pipelines
from kedro.pipeline import Pipeline

#: Run only when asked for: ``universe`` reads every fund's holdings, and
#: ``publish`` needs its outputs.
BATCH_PIPELINES = ("universe", "publish")

#: Left out of ``full``: ``lookthrough`` answers one ``isin`` parameter and is
#: run on its own.
ON_DEMAND_PIPELINES = ("lookthrough",)


def register_pipelines() -> dict[str, Pipeline]:
    """Register the project's pipelines.

    ``__default__`` builds the inputs (ingest, security master, consolidate,
    the regulator stores) and answers ``isin``. ``full`` builds everything the
    site needs, from the downloads to ``build/site/api``: every pipeline except
    ``lookthrough``, so ingest, security_master, consolidate, any regulator or
    registry pipeline, universe and publish. Kedro orders the nodes by the
    datasets they pass on, so the sum runs in that order.

    Returns:
        A mapping from pipeline names to ``Pipeline`` objects.
    """
    pipelines = find_pipelines(raise_errors=True)
    pipelines["__default__"] = sum(p for name, p in pipelines.items() if name not in BATCH_PIPELINES)
    pipelines["full"] = sum(
        p for name, p in pipelines.items() if name not in ON_DEMAND_PIPELINES and name != "__default__"
    )
    return pipelines
