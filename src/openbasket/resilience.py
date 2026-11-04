"""Keep a long run going when one optional download fails.

A full run reads dozens of sites and registers. Most of them add coverage
rather than being needed by everything after them, and any of them can be
down for an hour. :func:`soften` wraps such a node: when it raises, its
outputs are the copies the last successful run saved (read through the
catalog), with a warning in the log, and the run goes on. A node with no
saved copy uses its ``empty`` factory when it has one, and otherwise fails
as before. Which nodes are optional is decided in
:mod:`openbasket.pipeline_registry` (``SOFT_NODES``).
"""

from __future__ import annotations

import functools
import logging
from typing import Callable

from kedro.framework.hooks import hook_impl
from kedro.pipeline import Node, Pipeline

logger = logging.getLogger(__name__)

#: The run's catalog, set by :class:`CatalogHook` so a failed node can read its outputs' last saved copies.
_CATALOG = None

#: Node name -> what each failure fell back to, for the run's log and tests.
FALLBACKS: dict[str, str] = {}


class CatalogHook:
    @hook_impl
    def after_catalog_created(self, catalog) -> None:
        global _CATALOG
        _CATALOG = catalog


def soften(node: Node, empty: Callable[[], object] | None = None) -> Node:
    """``node`` whose failure falls back to its outputs' last saved copies (or ``empty()``)."""
    # The node as it was declared: a single output name, a list, or a dict of names.
    declared_inputs, declared = node._inputs, node._outputs
    outputs = list(node.outputs)
    func = node.func

    @functools.wraps(func)
    def run(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - any failure of an optional download
            saved = _CATALOG is not None and outputs and all(_CATALOG.exists(o) for o in outputs)
            if saved:
                logger.warning("%s failed (%s: %s); using the copy the last successful run saved", node.name,
                               type(exc).__name__, exc)
                FALLBACKS[node.name] = "last saved copy"
                values = [_CATALOG.load(o) for o in outputs]
            elif empty is not None:
                logger.warning("%s failed (%s: %s) and no earlier copy is saved; going on without it", node.name,
                               type(exc).__name__, exc)
                FALLBACKS[node.name] = "empty"
                got = empty()
                values = [got] if isinstance(declared, str) else list(got.values() if isinstance(got, dict) else got)
            else:
                raise
            if isinstance(declared, str):
                return values[0]
            if isinstance(declared, dict):
                return dict(zip(declared, values))
            return values

    return Node(run, declared_inputs, declared, name=node.name, tags=node.tags, namespace=node.namespace)


def soften_pipeline(pipeline: Pipeline, soft: dict[str, Callable[[], object] | None]) -> Pipeline:
    """``pipeline`` with every node named in ``soft`` wrapped by :func:`soften`."""
    return Pipeline([soften(n, soft[n.name]) if n.name in soft else n for n in pipeline.nodes])
