from kedro.pipeline import Node, Pipeline

from .nodes import (
    build,
    fetch_entities,
    fetch_esma_funds,
    fetch_firds,
    fetch_isin_lei,
    fetch_relationships,
    load_regulators,
    report,
)


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(fetch_firds, inputs="params:registry", outputs="registry_firds_funds", name="registry_fetch_firds_c"),
            Node(fetch_relationships, inputs="params:registry", outputs="registry_gleif_relationships", name="registry_fetch_gleif_rr"),
            Node(fetch_esma_funds, inputs="params:registry", outputs="registry_esma_funds", name="registry_fetch_esma_funds"),
            Node(
                fetch_entities,
                inputs=["registry_gleif_relationships", "registry_firds_funds", "registry_esma_funds", "params:registry"],
                outputs="registry_gleif_entities",
                name="registry_fetch_gleif_lei2",
            ),
            Node(
                fetch_isin_lei,
                inputs=["registry_gleif_entities", "registry_firds_funds", "params:registry"],
                outputs="registry_gleif_isin_lei",
                name="registry_fetch_gleif_isin_lei",
            ),
            Node(load_regulators, inputs="params:registry", outputs="registry_regulator_products", name="registry_load_regulators"),
            Node(
                build,
                inputs=[
                    "registry_gleif_entities",
                    "registry_gleif_relationships",
                    "registry_firds_funds",
                    "registry_esma_funds",
                    "registry_gleif_isin_lei",
                    "registry_regulator_products",
                    "params:registry",
                ],
                outputs=["fund_entities", "fund_registry"],
                name="registry_build",
            ),
            Node(
                report,
                inputs=["fund_entities", "fund_registry"],
                outputs=["registry_coverage", "registry_banks", "registry_domiciles", "registry_summary"],
                name="registry_report",
            ),
        ]
    )
