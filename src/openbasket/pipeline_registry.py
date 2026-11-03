"""Project pipelines."""
from __future__ import annotations

from kedro.framework.project import find_pipelines
from kedro.pipeline import Pipeline

#: Run only when asked for: ``universe`` reads every fund's holdings, and
#: ``publish`` needs its outputs.
BATCH_PIPELINES = ("universe", "publish")


def register_pipelines() -> dict[str, Pipeline]:
    """Register the project's pipelines.

    Returns:
        A mapping from pipeline names to ``Pipeline`` objects.
    """
    pipelines = find_pipelines(raise_errors=True)
    # ``prefetch`` reads every issuer file over the network, so it too runs only when asked for.
    on_request = (*BATCH_PIPELINES, "prefetch")
    pipelines["__default__"] = sum(p for name, p in pipelines.items() if name not in on_request)
    return pipelines
