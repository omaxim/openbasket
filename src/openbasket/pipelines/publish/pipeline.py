from kedro.pipeline import Node, Pipeline

from .nodes import publish


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                publish,
                ["universe", "universe_funds", "universe_constituents", "universe_run", "params:publish", "params:issuers"],
                "quality_report",
                name="publish",
            ),
        ]
    )
