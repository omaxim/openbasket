from kedro.pipeline import Node, Pipeline

from .nodes import fund_master, latest_filings, nport_holdings


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(latest_filings, "nport_parquet_manifest", "nport_latest_filings", name="latest_filings"),
            Node(fund_master, ["nport_latest_filings", "sec_fund_tickers"], "fund_master", name="fund_master"),
            Node(
                nport_holdings,
                ["nport_positions", "nport_latest_filings", "cusip_isin_map"],
                "nport_holdings",
                name="nport_holdings",
            ),
        ]
    )
