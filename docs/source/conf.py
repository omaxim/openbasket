"""Sphinx configuration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fund_lookthrough import __version__  # noqa: E402

project = "fund-lookthrough"
release = __version__
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3
autodoc_member_order = "bysource"
autodoc_typehints = "description"
html_theme = "furo"
html_static_path = ["_static"]
html_title = "fund-lookthrough"
