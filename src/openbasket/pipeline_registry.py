"""Project pipelines."""
from __future__ import annotations

from kedro.framework.project import find_pipelines
from kedro.pipeline import Pipeline

#: Run only when asked for: ``universe`` reads every fund's holdings,
#: ``publish`` needs its outputs, ``directory`` downloads about 120 MB of
#: exchange and regulator lists and ``reference`` queries ESMA FIRDS.
BATCH_PIPELINES = ("universe", "publish", "directory", "reference")


def register_pipelines() -> dict[str, Pipeline]:
    """Register the project's pipelines.

    Returns:
        A mapping from pipeline names to ``Pipeline`` objects.
    """
    pipelines = find_pipelines(raise_errors=True)
    pipelines["__default__"] = sum(p for name, p in pipelines.items() if name not in BATCH_PIPELINES)
    return pipelines
