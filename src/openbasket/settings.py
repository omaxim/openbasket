"""Project settings.

Loads ``.env`` from the project root so ``OPENFIGI_API_KEY`` and
``SEC_USER_AGENT`` reach the nodes without being written into configuration.
"""

from pathlib import Path

from dotenv import load_dotenv

from openbasket.resilience import CatalogHook

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

CONFIG_LOADER_ARGS = {
    "base_env": "base",
    "default_run_env": "local",
    # Environments (e.g. conf/public) override single nested keys, not whole sections.
    "merge_strategy": {"parameters": "soft"},
}

# Lets an optional download that fails fall back to its last saved copy (openbasket.resilience).
HOOKS = (CatalogHook(),)
