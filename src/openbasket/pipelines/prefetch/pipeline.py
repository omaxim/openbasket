from kedro.pipeline import Node, Pipeline

from .nodes import prefetch_issuer_holdings


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                prefetch_issuer_holdings,
                ["issuer_products", "params:prefetch", "params:universe", "params:issuers", "params:lookthrough",
                 "params:publish"],
                "prefetch_report",
                name="prefetch_issuer_holdings",
            ),
        ]
    )
