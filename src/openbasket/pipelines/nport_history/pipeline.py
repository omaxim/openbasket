from kedro.pipeline import Node, Pipeline

from .nodes import nport_quarter_coverage


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                nport_quarter_coverage,
                ["nport_parquet_manifest", "sec_fund_tickers"],
                "nport_history_coverage",
                name="nport_quarter_coverage",
            ),
        ]
    )
