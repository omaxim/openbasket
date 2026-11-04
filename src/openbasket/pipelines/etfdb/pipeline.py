from kedro.pipeline import Node, Pipeline

from .nodes import build_etfdb_meta, build_stand_ins, report_etfdb


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(build_etfdb_meta, inputs="params:etfdb", outputs="etfdb_meta", name="etfdb_meta"),
            Node(build_stand_ins, inputs=["etfdb_meta", "params:etfdb"], outputs="etfdb_stand_in_candidates",
                 name="etfdb_stand_ins"),
            Node(report_etfdb, inputs=["etfdb_meta", "params:etfdb"], outputs=["etfdb_summary", "etfdb_overlap"],
                 name="etfdb_report"),
        ]
    )
