"""Adapter modules that fail to import are skipped, not fatal."""

import logging

import pytest

from openbasket.pipelines.lookthrough.nodes import issuer_adapters
from openbasket.sources import issuers


@pytest.fixture
def broken_package(tmp_path, monkeypatch):
    """Adds a directory to the adapter package with a module that cannot be imported and one that registers twice."""
    (tmp_path / "zz_broken_dependency.py").write_text("import not_a_real_dependency_xyz\n")
    (tmp_path / "zz_duplicate.py").write_text(
        "from openbasket.sources.issuers import register\n"
        "from openbasket.sources.issuers import cvm_brazil\n"
        "register(cvm_brazil.ADAPTER)\n"
    )
    (tmp_path / "zz_syntax.py").write_text("def broken(:\n")
    issuers.safe_discover()  # the real modules are already registered
    monkeypatch.setattr(issuers, "__path__", [*issuers.__path__, str(tmp_path)])
    monkeypatch.setattr(issuers, "_FAILED", {})
    return tmp_path


def test_safe_discover_skips_modules_that_fail(broken_package, caplog):
    with caplog.at_level(logging.WARNING, logger="openbasket.sources.issuers"):
        found = issuers.safe_discover()
    assert {"cvm_brazil", "cnmv_spain"} <= set(found)
    failed = issuers.failed_modules()
    assert set(failed) == {"zz_broken_dependency", "zz_duplicate", "zz_syntax"}
    assert failed["zz_broken_dependency"].startswith("ModuleNotFoundError")
    assert "registered twice" in failed["zz_duplicate"]
    assert "zz_syntax" in caplog.text
    # Not retried, so the warnings are not repeated on every call.
    caplog.clear()
    issuers.safe_discover()
    assert caplog.text == ""


def test_pipelines_still_get_the_working_adapters(broken_package):
    with pytest.raises(ModuleNotFoundError):
        issuers.discover()
    assert {"cvm_brazil", "cnmv_spain"} <= set(issuer_adapters({}))
