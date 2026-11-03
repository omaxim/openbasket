from kedro.pipeline import Node, Pipeline

from .nodes import build_products, refresh_cda


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                refresh_cda,
                inputs=["params:reg_brazil.root", "params:reg_brazil.months", "params:reg_brazil.max_confidential_pct"],
                outputs="reg_brazil_manifest",
                name="refresh_cvm_cda",
            ),
            Node(
                build_products,
                inputs=["reg_brazil_manifest", "params:reg_brazil.root"],
                outputs="reg_brazil_products",
                name="build_cvm_products",
            ),
        ]
    )
