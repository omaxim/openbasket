"""Project pipelines."""
from __future__ import annotations

from kedro.framework.project import find_pipelines
from kedro.pipeline import Pipeline

from .pipelines.registry.nodes import empty_esma_funds, empty_firds
from .resilience import soften_pipeline

#: Run only when asked for: ``universe`` reads every fund's holdings,
#: ``publish`` needs its outputs, ``directory`` downloads about 120 MB of
#: exchange and regulator lists and ``reference`` queries ESMA FIRDS.
#: ``full`` (every pipeline but ``lookthrough``, see
#: :mod:`openbasket.pipelines.full`) holds the ones it needs.
BATCH_PIPELINES = ("universe", "publish", "full", "directory", "reference", "aggregates", "sectors")


#: Optional downloads: when one fails, the run goes on with the copy the last
#: successful run saved (see :mod:`openbasket.resilience`), or with the empty
#: table given here when there is none. Everything else still stops the run.
SOFT_NODES = {
    "registry_fetch_firds_c": empty_firds,
    "registry_fetch_esma_funds": empty_esma_funds,
    "registry_fetch_gleif_rr": None,
    "registry_fetch_gleif_lei2": None,
    "registry_fetch_gleif_isin_lei": None,
    "registry_load_regulators": None,
    "dir_esma_firds_debt": None,
    "dir_esma_firds_funds": None,
    "dir_fca_firds_funds": None,
    "dir_gleif_isin_lei": None,
    "dir_gleif_relationships": None,
    "firds_file_list": None,
    "findb_fetch": None,
    "refresh_cnmv_iic": None,
    "refresh_cmvm_carteiras": None,
    "refresh_edinet_reports": None,
    "refresh_fi_fondinnehav": None,
    "refresh_sfc_colombia": None,
    "swiss_public_registry": None,
    "swiss_sample_holdings": None,
}


def register_pipelines() -> dict[str, Pipeline]:
    """Register the project's pipelines.

    Returns:
        A mapping from pipeline names to ``Pipeline`` objects.
    """
    pipelines = {name: soften_pipeline(p, SOFT_NODES) for name, p in find_pipelines(raise_errors=True).items()}
    # ``prefetch`` reads every issuer file over the network, so it too runs only when asked for.
    on_request = (*BATCH_PIPELINES, "prefetch")
    pipelines["__default__"] = sum(p for name, p in pipelines.items() if name not in on_request)
    return pipelines
