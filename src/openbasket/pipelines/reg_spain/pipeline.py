from kedro.pipeline import Node, Pipeline

from .nodes import build_products, refresh_cnmv


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(refresh_cnmv, inputs="params:reg_spain.root", outputs="reg_spain_manifest", name="refresh_cnmv_iic"),
            Node(
                build_products,
                inputs=["reg_spain_manifest", "params:reg_spain.root"],
                outputs="reg_spain_products",
                name="build_cnmv_products",
            ),
        ]
    )
