from kedro.pipeline import Node, Pipeline

from .nodes import cusip_isin_map, nport_positions, security_master, sedol_isin_map, ticker_isin_map


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(nport_positions, "nport_parquet_manifest", "nport_positions", name="nport_positions"),
            Node(cusip_isin_map, "nport_positions", "cusip_isin_map", name="cusip_isin_map"),
            Node(security_master, ["nport_positions", "cusip_isin_map"], "security_master", name="security_master"),
            Node(ticker_isin_map, "security_master", "ticker_isin_map", name="ticker_isin_map"),
            Node(sedol_isin_map, "nport_parquet_manifest", "sedol_isin_map", name="sedol_isin_map"),
        ]
    )
