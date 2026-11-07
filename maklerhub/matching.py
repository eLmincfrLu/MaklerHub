"""Müştəri tələbi ↔ makler xidmət sahəsi uyğunluğu (0-100). Təmiz funksiyalar: baza və Flask yoxdur.

Tələb: dict / Row — deal_type, districts (siyahı və ya JSON mətn), budget_min, budget_max.
Xidmət sahəsi: agent_areas sətirləri — deal_type, district, price_min, price_max (NULL = həd yoxdur).

Qapı: tələbin deal_type-ı maklerin sahəsində yoxdursa — uyğunluq hesablanmır (None).
Bal: rayon 55 (ən azı bir ortaq rayon) + büdcə 45 × (aralıqların kəsişməsi / tələb aralığı).
"""
import json

DISTRICT_POINTS = 55
BUDGET_POINTS = 45
GOOD_SCORE = 50                 # "Uyğun olanlar" siyahısının həddi
NO_MIN_FACTOR = 0.7             # budget_min yoxdursa tələb aralığı [max × 0.7, max]


def _districts(value):
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            return []
    return list(value or [])


def area_for(areas, deal_type):
    """Maklerin həmin əməliyyat üzrə sahəsi: {'districts': set, 'min': int|None, 'max': int|None} və ya None."""
    rows = [a for a in areas if a["deal_type"] == deal_type]
    if not rows:
        return None
    mins = [a["price_min"] for a in rows]
    maxs = [a["price_max"] for a in rows]
    # adətən bütün sətirlərdə eyni aralıq olur (Ayarlar birlikdə saxlayır); fərqlidirsə ən geniş aralıq
    return {"districts": {a["district"] for a in rows},
            "min": None if any(m is None for m in mins) else min(mins),
            "max": None if any(m is None for m in maxs) else max(maxs)}


def request_range(budget_min, budget_max):
    hi = budget_max or 0
    lo = budget_min if budget_min else hi * NO_MIN_FACTOR
    return float(min(lo, hi)), float(hi)


def budget_ratio(req_lo, req_hi, agent_min, agent_max):
    """Tələb aralığının neçə hissəsi maklerin aralığına düşür (0..1)."""
    a_lo = float(agent_min) if agent_min is not None else 0.0
    a_hi = float(agent_max) if agent_max is not None else float("inf")
    if req_hi <= req_lo:  # nöqtə büdcə
        return 1.0 if a_lo <= req_hi <= a_hi else 0.0
    overlap = min(req_hi, a_hi) - max(req_lo, a_lo)
    return max(0.0, min(1.0, overlap / (req_hi - req_lo)))


def score(req, areas):
    """(bal, səbəblər) və ya qapıdan keçmirsə None. Səbəb: ("ok"|"partial"|"no", mətn)."""
    area = area_for(areas, req["deal_type"])
    if area is None:
        return None
    reasons = []
    common = [d for d in _districts(req["districts"]) if d in area["districts"]]
    points = 0
    if common:
        points += DISTRICT_POINTS
        names = ", ".join(common[:3])
        reasons.append(("ok", f"✓ {names} sizin rayonunuzdur" if len(common) == 1
                        else f"✓ {names} sizin rayonlarınızdır"))
    else:
        reasons.append(("no", "✗ Rayon xidmət sahənizdə deyil"))

    lo, hi = request_range(req["budget_min"], req["budget_max"])
    ratio = budget_ratio(lo, hi, area["min"], area["max"])
    points += round(BUDGET_POINTS * ratio)
    if ratio >= 0.999:
        reasons.append(("ok", "✓ Büdcə qiymət aralığınıza uyğundur"))
    elif ratio > 0:
        reasons.append(("partial", "△ Büdcə qismən uyğundur"))
    else:
        reasons.append(("no", "✗ Büdcə qiymət aralığınızdan kənardır"))
    return min(100, points), reasons


def rank(requests, areas):
    """Qapıdan keçən tələblər: [(tələb, bal, səbəblər), ...] (sırasız)."""
    out = []
    for r in requests:
        res = score(r, areas)
        if res is not None:
            out.append((r, res[0], res[1]))
    return out
