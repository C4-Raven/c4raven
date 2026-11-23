"""Which TAK.gov products an account sees: every CIV product, plus an FMS product (AUS,
MNWG) only when TAK.gov lists something for it that the CIV product doesn't. TAK.gov
answers a CIV-only account with the CIV plugins for every product."""

from raven.blueprints.raven_api import tak_gov_link_api as api


def plugin(name, version="1.0", rev=1):
    return {"package_name": name, "version": version, "revision_code": rev}


CIV = {
    "ATAK-CIV": [plugin("com.atak.plugins.a"), plugin("com.atak.plugins.b")],
    "WinTAK-CIV": [plugin("wintak.a")],
    "TAKX-CIV": [plugin("takx.a")],
}


def run(monkeypatch, lists):
    monkeypatch.setattr(api, "_fetch_plugin_list", lambda token, product, version: lists.get(product))
    return api.available_products({"access_token": "x"}, "5.5.0")


def test_civ_account_sees_only_civ_products(monkeypatch):
    lists = dict(CIV)
    # TAK.gov hands the same CIV plugins back for the FMS products
    for family, civ in (("ATAK", "ATAK-CIV"), ("WinTAK", "WinTAK-CIV"), ("TAKX", "TAKX-CIV")):
        lists[f"{family}-AUS"] = list(CIV[civ])
        lists[f"{family}-MNWG"] = list(CIV[civ])
    assert run(monkeypatch, lists) == ["ATAK-CIV", "WinTAK-CIV", "TAKX-CIV"]


def test_fms_account_sees_its_own_products(monkeypatch):
    lists = dict(CIV)
    lists["ATAK-AUS"] = CIV["ATAK-CIV"] + [plugin("com.atak.plugins.aus_only")]
    lists["ATAK-MNWG"] = list(CIV["ATAK-CIV"])
    lists["WinTAK-AUS"] = [plugin("wintak.a", version="2.0")]  # an AUS build of a CIV plugin
    assert run(monkeypatch, lists) == ["ATAK-CIV", "ATAK-AUS", "WinTAK-CIV", "WinTAK-AUS", "TAKX-CIV"]


def test_errors_and_empty_lists_hide_fms_products(monkeypatch):
    lists = {"ATAK-CIV": None, "ATAK-AUS": None, "ATAK-MNWG": []}
    assert run(monkeypatch, lists) == ["ATAK-CIV", "WinTAK-CIV", "TAKX-CIV"]
