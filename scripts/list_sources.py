"""List the holdings sources, their redistribution class and whether a run uses them.

Usage::

    python scripts/list_sources.py [--env local]

A run uses the adapters ``issuers.enabled`` names (every adapter that is on
by default when it is null) minus ``issuers.disabled``, read from
``conf/base`` with ``conf/<env>`` on top, as ``kedro run --env <env>`` would.
To switch one on, name it in ``issuers.enabled`` in
``conf/local/parameters.yml``; see docs/source/running-locally.md.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def issuers_params(env: str = "local") -> dict:
    """``issuers`` as Kedro would load it for ``--env env``."""
    from kedro.config import OmegaConfigLoader

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # OmegaConf's resolver deprecation notice
        loader = OmegaConfigLoader(
            conf_source=str(ROOT / "conf"), base_env="base", default_run_env=env,
            merge_strategy={"parameters": "soft"},
        )
        return loader["parameters"].get("issuers") or {}


def rows(env: str = "local") -> list[tuple[str, str, str, str]]:
    """(name, redistribution, on by default, used) for iShares, SPDR and every adapter."""
    from openbasket.sources import redistribution
    from openbasket.sources.issuers import safe_discover, select

    adapters = safe_discover()
    params = issuers_params(env)
    used = select(adapters, params.get("enabled"), params.get("disabled"))
    out = [
        # iShares and SPDR are not adapters: their lists are chosen by site and region.
        ("ishares", redistribution("ishares"), "on", ", ".join(params.get("ishares_sites") or []) or "no"),
        ("spdr", redistribution("spdr"), "on", ", ".join(params.get("spdr_regions") or []) or "no"),
    ]
    out += [
        (name, a.redistribution, "on" if a.enabled_by_default else "off", "yes" if name in used else "no")
        for name, a in sorted(adapters.items())
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", default="local", help="Kedro environment on top of conf/base (default: local)")
    args = parser.parse_args(argv)

    from openbasket.sources.issuers import failed_modules

    print(f"{'source':<24} {'redistribution':<15} {'default':<8} used with --env {args.env}")
    for name, redist, default, used in rows(args.env):
        print(f"{name:<24} {redist:<15} {default:<8} {used}")
    for module, error in sorted(failed_modules().items()):
        print(f"(skipped {module}: {error})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
