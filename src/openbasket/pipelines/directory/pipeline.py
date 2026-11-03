from kedro.pipeline import Node, Pipeline

from . import nodes as n

FUND_LISTINGS = ["dir_six_funds", "dir_xetra", "dir_euronext_etfs", "dir_esma_firds_funds", "dir_fca_firds_funds"]
BOND_LISTINGS = ["dir_six_bonds", "dir_euronext_bonds", "dir_ecb_eligible", "dir_esma_firds_debt"]


def create_pipeline(**kwargs) -> Pipeline:
    p = "params:directory"
    return Pipeline(
        [
            Node(n.dir_six_funds, p, "dir_six_funds", name="dir_six_funds"),
            Node(n.dir_six_bonds, p, "dir_six_bonds", name="dir_six_bonds"),
            Node(n.dir_xetra, p, "dir_xetra", name="dir_xetra"),
            Node(n.dir_euronext_etfs, p, "dir_euronext_etfs", name="dir_euronext_etfs"),
            Node(n.dir_euronext_bonds, p, "dir_euronext_bonds", name="dir_euronext_bonds"),
            Node(n.dir_ecb_eligible, p, "dir_ecb_eligible", name="dir_ecb_eligible"),
            Node(n.dir_esma_firds_funds, p, "dir_esma_firds_funds", name="dir_esma_firds_funds"),
            Node(n.dir_fca_firds_funds, p, "dir_fca_firds_funds", name="dir_fca_firds_funds"),
            Node(n.dir_esma_firds_debt, p, "dir_esma_firds_debt", name="dir_esma_firds_debt"),
            Node(n.dir_gleif_isin_lei, [p, *FUND_LISTINGS, *BOND_LISTINGS], "dir_gleif_isin_lei", name="dir_gleif_isin_lei"),
            Node(n.dir_gleif_relationships, p, "dir_gleif_relationships", name="dir_gleif_relationships"),
            Node(
                n.dir_gleif_entities,
                [p, "dir_gleif_relationships", "dir_gleif_isin_lei", *FUND_LISTINGS],
                "dir_gleif_entities",
                name="dir_gleif_entities",
            ),
            Node(
                n.fund_directory,
                [p, "dir_gleif_isin_lei", "dir_gleif_relationships", "dir_gleif_entities", *FUND_LISTINGS],
                "fund_directory",
                name="fund_directory",
            ),
            Node(n.bond_directory, ["dir_gleif_isin_lei", *BOND_LISTINGS], "bond_directory", name="bond_directory"),
            Node(n.directory_coverage, "fund_directory", "directory_coverage", name="directory_coverage"),
        ]
    )
