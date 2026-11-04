from kedro.pipeline import Node, Pipeline

from .nodes import bank_coverage, build_registry, public_registry, sample_holdings


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(build_registry, inputs="params:swiss", outputs="swiss_fund_registry", name="build_swiss_registry"),
            Node(public_registry, inputs="swiss_fund_registry", outputs="swiss_fund_registry_public", name="swiss_public_registry"),
            Node(sample_holdings, inputs="params:swiss", outputs=["swiss_sample_holdings", "swiss_sample_runs"],
                 name="swiss_sample_holdings"),
            Node(bank_coverage, inputs=["swiss_fund_registry", "swiss_sample_runs", "params:swiss"], outputs="swiss_bank_coverage",
                 name="swiss_bank_coverage"),
        ]
    )
