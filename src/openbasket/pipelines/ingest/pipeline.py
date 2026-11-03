from kedro.pipeline import Node, Pipeline

from .nodes import download_fund_tickers, download_issuer_products, download_nport, nport_to_parquet


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                download_nport,
                inputs=["params:nport.quarters", "params:nport.raw_dir", "params:nport.overwrite"],
                outputs="nport_raw_manifest",
                name="download_nport",
            ),
            Node(
                nport_to_parquet,
                inputs=["nport_raw_manifest", "params:nport.parquet_dir"],
                outputs="nport_parquet_manifest",
                name="nport_to_parquet",
            ),
            Node(download_fund_tickers, inputs=None, outputs="sec_fund_tickers", name="download_fund_tickers"),
            Node(
                download_issuer_products,
                inputs=["params:issuers.ishares_sites", "params:issuers.spdr_regions"],
                outputs="issuer_products",
                name="download_issuer_products",
            ),
        ]
    )
