from kedro.pipeline import Node, Pipeline

from .nodes import build_findb_funds, compare_findb, fetch_financedatabase


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(fetch_financedatabase, inputs="params:financedatabase", outputs="findb_manifest", name="findb_fetch"),
            Node(
                build_findb_funds,
                inputs=["findb_manifest", "params:financedatabase"],
                outputs=["findb_funds", "findb_summary"],
                name="findb_build",
            ),
            Node(compare_findb, inputs=["findb_funds", "params:financedatabase"], outputs="findb_overlap", name="findb_compare"),
        ]
    )
