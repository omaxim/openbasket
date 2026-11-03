from kedro.pipeline import Node, Pipeline

from .nodes import build_universe, run_universe, select_universe


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                build_universe,
                ["issuer_products", "fund_master", "security_master", "params:universe"],
                "universe",
                name="build_universe",
            ),
            Node(select_universe, ["universe", "params:universe"], "universe_selected", name="select_universe"),
            Node(
                run_universe,
                ["universe_selected", "nport_holdings", "security_master", "fund_master", "ticker_isin_map",
                 "issuer_products", "cusip_isin_map", "sedol_isin_map", "params:lookthrough", "params:universe",
                 "params:publish.sources"],
                ["universe_funds", "universe_constituents", "universe_run"],
                name="run_universe",
            ),
        ]
    )
