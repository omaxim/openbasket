from kedro.pipeline import Node, Pipeline

from .nodes import edinet_reports, refresh_edinet


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(refresh_edinet, inputs="params:reg_japan", outputs="reg_japan_manifest", name="refresh_edinet_reports"),
            Node(edinet_reports, inputs=["reg_japan_manifest", "params:reg_japan.root"], outputs="reg_japan_reports",
                 name="list_edinet_reports"),
        ]
    )
