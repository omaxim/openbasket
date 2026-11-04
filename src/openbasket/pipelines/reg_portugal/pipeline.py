from kedro.pipeline import Node, Pipeline

from .nodes import build_products, refresh_cmvm


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(refresh_cmvm, inputs="params:reg_portugal.root", outputs="reg_portugal_manifest",
                 name="refresh_cmvm_carteiras"),
            Node(
                build_products,
                inputs=["reg_portugal_manifest", "params:reg_portugal.root"],
                outputs="reg_portugal_products",
                name="build_cmvm_products",
            ),
        ]
    )
