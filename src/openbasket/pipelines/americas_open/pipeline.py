from kedro.pipeline import Node, Pipeline

from .nodes import build_products, refresh_sfc


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(refresh_sfc, inputs="params:americas_open.root", outputs="americas_open_manifest",
                 name="refresh_sfc_colombia"),
            Node(
                build_products,
                inputs=["americas_open_manifest", "params:americas_open.root"],
                outputs="americas_open_products",
                name="build_sfc_colombia_products",
            ),
        ]
    )
