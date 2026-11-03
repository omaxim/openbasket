from kedro.pipeline import Node, Pipeline

from .nodes import firds_download, firds_file_list, firds_instruments, firds_lookup, firds_to_parquet


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                firds_lookup,
                inputs=["params:isin", "params:firds.cache_dir"],
                outputs="firds_lookup",
                name="firds_lookup",
                tags=["firds"],
            ),
            Node(firds_file_list, inputs="params:firds.bulk", outputs="firds_files", name="firds_file_list", tags=["firds", "firds_bulk"]),
            Node(
                firds_download,
                inputs=["firds_files", "params:firds.bulk"],
                outputs="firds_raw_manifest",
                name="firds_download",
                tags=["firds", "firds_bulk"],
            ),
            Node(
                firds_to_parquet,
                inputs=["firds_raw_manifest", "params:firds.bulk"],
                outputs="firds_parquet_manifest",
                name="firds_to_parquet",
                tags=["firds", "firds_bulk"],
            ),
            Node(
                firds_instruments,
                inputs="firds_parquet_manifest",
                outputs="firds_instruments",
                name="firds_instruments",
                tags=["firds", "firds_bulk"],
            ),
        ]
    )
