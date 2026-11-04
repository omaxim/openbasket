from kedro.pipeline import Node, Pipeline

from ...aggregates import build, share


def share_aggregates(doc: dict, params: dict, publish_params: dict) -> dict:
    """Commit the written aggregates file, and nothing else, when ``aggregates.share.enabled`` (see :func:`share`)."""
    return share(doc, params, publish_params["aggregates_path"])


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                build,
                ["universe_funds", "universe_constituents", "params:aggregates", "sector_map", "params:publish"],
                "public_aggregates",
                name="build_aggregates",
            ),
            # Reads public_aggregates back from disk, so it runs once the file is written.
            Node(share_aggregates, ["public_aggregates", "params:aggregates", "params:publish"], "aggregates_shared",
                 name="share_aggregates"),
        ]
    )
