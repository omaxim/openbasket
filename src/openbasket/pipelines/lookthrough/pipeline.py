from kedro.pipeline import Node, Pipeline

from .nodes import build_output, parse_isins, run_lookthrough


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(parse_isins, "params:isin", "requested_isins", name="parse_isins"),
            Node(
                run_lookthrough,
                ["requested_isins", "nport_holdings", "security_master", "fund_master", "ticker_isin_map", "issuer_products",
                 "cusip_isin_map", "sedol_isin_map", "params:lookthrough"],
                ["lookthrough_positions", "lookthrough_unidentified", "lookthrough_summary"],
                name="run_lookthrough",
            ),
            Node(
                build_output,
                ["lookthrough_positions", "lookthrough_summary", "params:lookthrough"],
                "lookthrough",
                name="build_output",
            ),
        ]
    )
