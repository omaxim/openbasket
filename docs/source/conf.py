"""Sphinx configuration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from openbasket import __version__  # noqa: E402

project = "openbasket"
author = "Leith Research"
copyright = "2026 Leith Research"
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

# The docs are served under /docs/ next to the landing page, and styled like it
# (and like www.leithresearch.org): same tokens, same fonts, light only.
SITE_URL = "https://openbasket.leithresearch.org/"
REPO_URL = "https://github.com/omaxim/openbasket"

html_theme = "furo"
html_title = "openbasket"
html_baseurl = f"{SITE_URL}docs/"
html_static_path = ["_static"]
templates_path = ["_templates"]
html_favicon = "_static/favicon.svg"
html_css_files = [
    "https://fonts.googleapis.com/css2?family=Sora:wght@700;800&family=Instrument+Serif:ital@0;1"
    "&family=Inter:wght@400;500;600&display=swap",
    "openbasket.css",
]
html_js_files = ["light.js"]
html_show_sphinx = False
html_context = {"site_url": SITE_URL, "repo_url": REPO_URL}
pygments_style = "friendly"
pygments_dark_style = "friendly"

_tokens = {
    "paper": "#f6f2ec",
    "paper-2": "#ede7de",
    "ink": "#1b1a19",
    "ink-2": "#4a4744",
    "muted": "#7d7873",
    "line": "#d9d2c7",
    "wine": "#9c3848",
    "wine-2": "#7e2c3a",
}
_colors = {
    "color-brand-primary": _tokens["wine"],
    "color-brand-content": _tokens["wine"],
    "color-brand-visited": _tokens["wine-2"],
    "color-foreground-primary": _tokens["ink"],
    "color-foreground-secondary": _tokens["ink-2"],
    "color-foreground-muted": _tokens["muted"],
    "color-foreground-border": _tokens["line"],
    "color-background-primary": _tokens["paper"],
    "color-background-secondary": _tokens["paper-2"],
    "color-background-hover": "#e4ddd2",
    "color-background-hover--transparent": "#e4ddd200",
    "color-background-border": _tokens["line"],
    "color-background-item": _tokens["line"],
    "color-announcement-background": _tokens["ink"],
    "color-announcement-text": _tokens["paper"],
    "color-highlight-on-target": "#f3e3e1",
    "color-inline-code-background": _tokens["paper-2"],
    "color-sidebar-background": _tokens["paper-2"],
    "color-sidebar-background-border": _tokens["line"],
    "color-sidebar-link-text--top-level": _tokens["ink"],
    "color-sidebar-item-background--hover": "#e4ddd2",
    "color-sidebar-search-background": _tokens["paper"],
    "color-sidebar-search-border": _tokens["line"],
    "color-link": _tokens["wine"],
    "color-link--hover": _tokens["wine-2"],
    "color-link-underline": "#9c384833",
    "color-link-underline--hover": _tokens["wine-2"],
    "color-table-header-background": _tokens["paper-2"],
    "color-table-border": _tokens["line"],
    "color-card-background": _tokens["paper"],
    "color-card-border": _tokens["line"],
    "color-admonition-background": _tokens["paper-2"],
}
_fonts = {
    "font-stack": '"Inter", system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif',
    "font-stack--headings": '"Sora", "Inter", system-ui, sans-serif',
    "font-stack--monospace": 'ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
}

html_theme_options = {
    "sidebar_hide_name": True,
    "light_css_variables": {**_colors, **_fonts},
    # The Leith site has no dark mode; dark mode gets the same light palette.
    "dark_css_variables": _colors,
    "announcement": (
        f'<a href="{SITE_URL}">openbasket</a> is a fund look-through tool: the site shows holdings from sources '
        "that allow republication, and you run it yourself for the rest. Not investment advice and not affiliated "
        "with any issuer. "
        f'<a href="{SITE_URL}">Search funds &rarr;</a>'
    ),
    "footer_icons": [
        {
            "name": "GitHub",
            "url": REPO_URL,
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" viewBox="0 0 16 16"><path '
                'fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 '
                "0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53"
                ".63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 "
                "0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36"
                ".09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 "
                "3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 "
                '16 8c0-4.42-3.58-8-8-8z"></path></svg>'
            ),
            "class": "",
        },
    ],
    "source_repository": f"{REPO_URL}/",
    "source_branch": "main",
    "source_directory": "docs/source/",
}
