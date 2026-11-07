"""Uyğunluq mühərriki — bazasız unit testlər (H1)."""
from maklerhub import matching


def area(deal, districts, lo=None, hi=None):
    return [{"deal_type": deal, "district": d, "price_min": lo, "price_max": hi} for d in districts]


def req(deal="rent", districts=("Yasamal",), bmin=500, bmax=900):
    return {"deal_type": deal, "districts": list(districts), "budget_min": bmin, "budget_max": bmax}


def test_full_match_is_100():
    sc, reasons = matching.score(req(), area("rent", ["Yasamal", "Nəsimi"], 400, 1000))
    assert sc == 100
    assert [k for k, _ in reasons] == ["ok", "ok"]
    assert "Yasamal sizin rayonunuzdur" in reasons[0][1]


def test_district_ok_budget_out_is_55():
    sc, reasons = matching.score(req(bmin=500, bmax=900), area("rent", ["Yasamal"], 2000, 3000))
    assert sc == 55
    assert reasons[1][0] == "no"


def test_no_district_at_most_45_and_not_good():
    sc, reasons = matching.score(req(districts=["Xətai"]), area("rent", ["Yasamal"], 400, 1000))
    assert sc <= 45
    assert sc < matching.GOOD_SCORE
    assert reasons[0] == ("no", "✗ Rayon xidmət sahənizdə deyil")


def test_partial_budget():
    # tələb 500-900, makler 700-2000 → kəsişmə 200/400 = 0.5 → 23 (45*0.5=22.5 → 22 və ya 23)
    sc, reasons = matching.score(req(), area("rent", ["Yasamal"], 700, 2000))
    assert 55 + 22 <= sc <= 55 + 23
    assert reasons[1][0] == "partial"


def test_budget_min_missing_uses_70_percent():
    # min yoxdur: aralıq [700, 1000]; makler 400-850 → kəsişmə 150/300 = 0.5
    lo, hi = matching.request_range(None, 1000)
    assert (lo, hi) == (700.0, 1000.0)
    sc, _ = matching.score(req(bmin=None, bmax=1000), area("rent", ["Yasamal"], 400, 850))
    assert sc in (77, 78)
    # makler aralığı tam əhatə edir
    sc, _ = matching.score(req(bmin=None, bmax=1000), area("rent", ["Yasamal"], None, None))
    assert sc == 100


def test_gate_other_deal_type_is_none():
    assert matching.score(req(deal="sale"), area("rent", ["Yasamal"], 400, 1000)) is None
    assert matching.score(req(), []) is None


def test_rent_and_sale_agent_uses_matching_deal():
    areas = area("rent", ["Yasamal"], 400, 1000) + area("sale", ["Xətai"], 100_000, 200_000)
    assert matching.score(req(), areas)[0] == 100                                   # kirayə sahəsi
    sale = req(deal="sale", districts=["Yasamal"], bmin=120_000, bmax=150_000)
    sc, reasons = matching.score(sale, areas)                                      # satış: rayon yox, büdcə tam
    assert sc == 45 and reasons[0][0] == "no"
    sale_ok = req(deal="sale", districts=["Xətai"], bmin=120_000, bmax=150_000)
    assert matching.score(sale_ok, areas)[0] == 100


def test_point_budget_and_open_ranges():
    assert matching.budget_ratio(800, 800, 400, 1000) == 1.0
    assert matching.budget_ratio(800, 800, 900, None) == 0.0
    assert matching.budget_ratio(500, 900, None, None) == 1.0


def test_districts_as_json_text():
    r = {"deal_type": "rent", "districts": '["Nəsimi", "Yasamal"]', "budget_min": 500, "budget_max": 900}
    sc, reasons = matching.score(r, area("rent", ["Yasamal", "Nəsimi"], 400, 1000))
    assert sc == 100 and "rayonlarınızdır" in reasons[0][1]


def test_rank_filters_gate():
    reqs = [req(), req(deal="sale"), req(districts=["Xətai"])]
    ranked = matching.rank(reqs, area("rent", ["Yasamal"], 400, 1000))
    assert [s for _, s, _ in ranked] == [100, 45]
