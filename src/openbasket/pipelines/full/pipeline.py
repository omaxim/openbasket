"""Every pipeline but the one-fund look-through, as one run.

``kedro run --pipeline=full`` goes from the downloads to the static API:
ingest, any regulator or registry pipeline (reg_brazil, reg_spain, ...),
security_master, consolidate, universe and publish. Kedro orders the nodes
by the datasets they pass on, so they run in that order.

It is a pipeline package of its own, not only an entry in the registry,
because Kedro imports just the requested pipeline's package when one is
named on the command line. A pipeline package added later joins ``full``
without a change here.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from kedro.pipeline import Pipeline

#: Pipeline packages left out: this one, and ``lookthrough``, which answers
#: one ``isin`` parameter and is run on its own.
EXCLUDED = ("full", "lookthrough")


def pipeline_names() -> list[str]:
    """The sibling pipeline packages ``full`` runs, by name."""
    root = Path(__file__).resolve().parents[1]
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "__init__.py").is_file() and d.name not in EXCLUDED and not d.name.startswith((".", "_"))
    )


def create_pipeline(**kwargs) -> Pipeline:
    package = __name__.rsplit(".", 2)[0]  # openbasket.pipelines
    parts = [importlib.import_module(f"{package}.{name}").create_pipeline() for name in pipeline_names()]
    return sum(parts, Pipeline([]))
