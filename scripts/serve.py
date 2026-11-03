"""Serve your own openbasket build on http://localhost:8000/.

Run it after ``kedro run --pipeline=full``. It puts the same site together
as the public one, from your own data:

    build/pages-local/            the landing page (site/)
    build/pages-local/docs/       the Sphinx HTML (built first if missing)
    build/pages-local/api/v1/     your static JSON API (build/site/api)

and serves it with Python's own web server. The API is the same set of
static files the public site serves (docs/source/data-api.md), so anything
that reads it works against your copy too::

    curl -s http://localhost:8000/api/v1/meta.json
    curl -s http://localhost:8000/api/v1/index.json
    curl -s http://localhost:8000/api/v1/funds/IE00B5BMR087.json

Usage::

    python scripts/serve.py [--port 8000] [--host 127.0.0.1] [--rebuild-docs] [--no-assemble]

The server only listens on this machine (127.0.0.1) unless ``--host`` says
otherwise. Every response says ``Cache-Control: no-store``, so a rebuilt
API shows up on the next reload. Stop it with Ctrl+C.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import subprocess
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_SRC = ROOT / "docs" / "source"
DOCS = ROOT / "docs" / "build" / "html"
API = ROOT / "build" / "site" / "api"
OUT = ROOT / "build" / "pages-local"

#: Content types set explicitly, so they do not depend on the machine's mimetypes table.
CONTENT_TYPES = {
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".xml": "application/xml",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _load_assemble_site():
    spec = importlib.util.spec_from_file_location("assemble_site", Path(__file__).with_name("assemble_site.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_docs(src: Path = DOCS_SRC, out: Path = DOCS) -> Path:
    """Build the Sphinx HTML into ``out`` with this Python's Sphinx."""
    print(f"Building the docs into {out} ...", flush=True)
    subprocess.run([sys.executable, "-m", "sphinx", "-b", "html", "-q", str(src), str(out)], check=True)
    return out


class Handler(SimpleHTTPRequestHandler):
    """Static files with fixed content types, no caching and the site's own 404 page."""

    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **CONTENT_TYPES}

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path):
        # Like GitHub Pages: a folder without index.html is not found, not listed.
        self.send_error(HTTPStatus.NOT_FOUND, "File not found")
        return None

    def send_error(self, code, message=None, explain=None):
        page = Path(self.directory) / "404.html"
        if code != HTTPStatus.NOT_FOUND or not page.is_file():
            return super().send_error(code, message, explain)
        body = page.read_bytes()
        self.send_response(code, message)
        self.send_header("Content-Type", CONTENT_TYPES[".html"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return None


def make_server(directory: Path, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """A server for ``directory``; port 0 picks a free one."""
    handler = functools.partial(Handler, directory=str(directory))
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1, this machine only)")
    parser.add_argument("--api", type=Path, default=API, help="folder holding v1/ (default: build/site/api)")
    parser.add_argument("--docs", type=Path, default=DOCS, help="Sphinx HTML (default: docs/build/html, built if missing)")
    parser.add_argument("--out", type=Path, default=OUT, help="folder to assemble into (default: build/pages-local)")
    parser.add_argument("--rebuild-docs", action="store_true", help="build the docs again even if they exist")
    parser.add_argument("--no-assemble", action="store_true", help="serve --out as it is, without putting it together again")
    args = parser.parse_args(argv)

    if not args.no_assemble:
        if not (args.api / "v1" / "meta.json").is_file():
            print(f"serve: no API at {args.api} (expected {args.api / 'v1' / 'meta.json'}).\n"
                  "Build it first: kedro run --pipeline=full", file=sys.stderr)
            return 1
        try:
            if args.rebuild_docs or not (args.docs / "index.html").is_file():
                build_docs(out=args.docs)
            out = _load_assemble_site().assemble(args.docs, args.api, args.out, domain="localhost")
        except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"serve: {exc}", file=sys.stderr)
            return 1
        print(f"Assembled the site in {out}")
    elif not (args.out / "index.html").is_file():
        print(f"serve: nothing to serve in {args.out}; run without --no-assemble first", file=sys.stderr)
        return 1

    try:
        server = make_server(args.out, args.host, args.port)
    except OSError as exc:
        print(f"serve: cannot listen on {args.host}:{args.port} ({exc.strerror}); try --port 8001", file=sys.stderr)
        return 1
    host = "localhost" if args.host in ("127.0.0.1", "localhost", "::1") else args.host
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Serving openbasket at {url}  (API: {url}api/v1/meta.json; Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
