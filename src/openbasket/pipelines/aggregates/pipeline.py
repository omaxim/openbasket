from kedro.pipeline import Node, Pipeline

from ...aggregates import build


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                build,
                ["universe_funds", "universe_constituents", "params:aggregates", "sector_map"],
                "public_aggregates",
                name="build_aggregates",
            ),
        ]
    )
