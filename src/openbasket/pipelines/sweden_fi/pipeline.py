from kedro.pipeline import Node, Pipeline

from .nodes import build_products, refresh_fi


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(refresh_fi, inputs="params:sweden_fi.root", outputs="sweden_fi_manifest", name="refresh_fi_fondinnehav"),
            Node(
                build_products,
                inputs=["sweden_fi_manifest", "params:sweden_fi.root"],
                outputs="sweden_fi_products",
                name="build_fi_products",
            ),
        ]
    )
