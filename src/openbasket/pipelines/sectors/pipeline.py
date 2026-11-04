from kedro.pipeline import Node, Pipeline

from ..publish.nodes import build_sector_map


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline([Node(build_sector_map, "params:sectors", "sector_map", name="build_sector_map")])
