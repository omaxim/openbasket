from kedro.pipeline import Node, Pipeline

from ...aggregates import build, share
from ...sources import fund_identity
from ..ingest.nodes import local_only_identity, local_only_issuers


def build_identity(publish_params: dict, issuers_params: dict | None, doc: dict) -> dict:
    """Write the identity of every ``local_only`` issuer's fund (ISIN, name, issuer) for the public build.

    The rows are the ones the public build would make from the open registries
    and cached issuer lists on this machine (see
    :func:`openbasket.sources.fund_identity.identity_rows`); the public build
    has neither, and reads this file instead (``publish.identity.identity_files``).
    """
    path = publish_params.get("identity_file")
    if not path:
        return {"written": False, "reason": "publish.identity_file is not set"}
    withheld, adapters = local_only_issuers(issuers_params)
    cfg = {**publish_params, "identity": {**(publish_params.get("identity") or {}), "identity_files": []}}
    rows = local_only_identity(withheld, adapters, cfg)
    if rows.empty:
        return {"written": False, "reason": "no registry or issuer list on this machine"}
    out = fund_identity.write_identity_file(rows, path)
    return {"written": True, "path": path, "n_funds": len(out), "issuers": out["issuer"].value_counts().to_dict()}


def share_aggregates(doc: dict, params: dict, publish_params: dict, identity: dict) -> dict:
    """Commit the aggregates and identity files, and nothing else, when ``aggregates.share.enabled`` (see :func:`share`)."""
    extra = []
    if identity.get("written"):
        fund_identity.read_identity_file(identity["path"])  # validated again as it will be read
        extra.append(identity["path"])
    return share(doc, params, publish_params["aggregates_path"], extra)


def create_pipeline(**kwargs) -> Pipeline:
    return Pipeline(
        [
            Node(
                build,
                ["universe_funds", "universe_constituents", "params:aggregates", "sector_map", "params:publish"],
                "public_aggregates",
                name="build_aggregates",
            ),
            Node(build_identity, ["params:publish", "params:issuers", "public_aggregates"], "public_identity",
                 name="build_public_identity"),
            # Reads public_aggregates back from disk, so it runs once the file is written.
            Node(share_aggregates, ["public_aggregates", "params:aggregates", "params:publish", "public_identity"],
                 "aggregates_shared", name="share_aggregates"),
        ]
    )
