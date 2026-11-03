"""The redistribution contract: adapter fields, module constants, safe discovery and the lookup helper."""

import pandas as pd
import pytest

from openbasket import sources
from openbasket.sources import issuers
from openbasket.sources.issuers import IssuerAdapter


def _adapter(name, **kw):
    return IssuerAdapter(
        name=name, title=name.title(), homepage="https://x.example/", credit="x", licence="x", regions=("XX",),
        products=lambda: pd.DataFrame(columns=issuers.PRODUCT_COLUMNS), holdings=lambda p, ctx: None, **kw,
    )


def test_adapter_defaults_are_local_only_and_enabled():
    a = _adapter("plain")
    assert a.redistribution == "local_only" and a.enabled_by_default is True


def test_adapter_rejects_an_unknown_class():
    with pytest.raises(ValueError, match="redistribution"):
        _adapter("bad", redistribution="open")


def test_select_skips_adapters_off_by_default_unless_named():
    reg = {"on": _adapter("on"), "off": _adapter("off", enabled_by_default=False)}
    assert set(issuers.select(reg)) == {"on"}
    assert set(issuers.select(reg, enabled=["on", "off"])) == {"on", "off"}
    assert set(issuers.select(reg, enabled=["off"], disabled=["off"])) == set()


@pytest.mark.parametrize(
    "source_id, expected",
    [
        ("nport", "public"),
        ("security_master", "public"),
        ("tickerinside", "public"),
        ("openfigi", "public"),
        ("sec", "public"),
        ("cvm_brazil", "public"),
        ("cnmv_spain", "public"),
        ("ishares", "local_only"),
        ("spdr", "local_only"),
        ("edinet_japan", "local_only"),
        ("issuers", "local_only"),
        ("no_such_source", "local_only"),
        ("../etc", "local_only"),
        ("", "local_only"),
        (None, "local_only"),
        ("NPORT", "public"),
    ],
)
def test_redistribution_of_known_and_unknown_sources(source_id, expected):
    assert sources.redistribution(source_id) == expected


def test_registered_adapters_answer_for_themselves(monkeypatch):
    issuers.safe_discover()
    monkeypatch.setitem(issuers._REGISTRY, "openreg", _adapter("openreg", redistribution="public"))
    monkeypatch.setitem(issuers._REGISTRY, "shopco", _adapter("shopco"))
    assert sources.redistribution("openreg") == "public"
    assert sources.redistribution("shopco") == "local_only"


def test_every_registered_adapter_declares_a_valid_class():
    for name, a in issuers.safe_discover().items():
        assert a.redistribution in issuers.REDISTRIBUTION_CLASSES, name
        assert isinstance(a.enabled_by_default, bool), name


def test_a_broken_adapter_module_does_not_change_the_answer(tmp_path, monkeypatch):
    (tmp_path / "zz_broken_dependency.py").write_text("import not_a_real_dependency_xyz\n")
    issuers.safe_discover()
    monkeypatch.setattr(issuers, "__path__", [*issuers.__path__, str(tmp_path)])
    monkeypatch.setattr(issuers, "_FAILED", {})
    assert sources.redistribution("cvm_brazil") == "public"
    assert sources.redistribution("zz_broken_dependency") == "local_only"
    assert "zz_broken_dependency" in issuers.failed_modules()
