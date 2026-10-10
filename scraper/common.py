"""Bütün scraper-lər üçün ortaq: xətalar və "şübhəli qiymət" qaydası."""


class ScrapeError(Exception):
    pass


class BlockedError(ScrapeError):
    """Sayt sorğunu rədd etdi (401/403/429/JSON olmayan cavab). Davam etmək olmaz."""


# "Şübhəli" qiymət hədləri (AZN). İstədikdə dəyişdirilə bilər.
RENT_MIN_TOTAL = 100        # aylıq kirayə bundan azdırsa
RENT_MIN_PER_M2 = 3         # aylıq kirayə 1 m²-ə bundan azdırsa
SALE_MIN_TOTAL = 10000      # satış qiyməti bundan azdırsa
SALE_MIN_PER_M2 = 400       # satış 1 m²-ə bundan azdırsa


def price_flags(deal, period, price, area, currency):
    """Qiymət məntiqsizdirsə səbəblərin siyahısı (boş siyahı = normal)."""
    flags = []
    if currency != "AZN":
        flags.append("valyuta " + str(currency))
    if price is None or price <= 0:
        flags.append("qiymət yoxdur")
        return flags
    per_m2 = (price / area) if area else None
    if deal == "rent" and period == "month":
        if price < RENT_MIN_TOTAL:
            flags.append(f"aylıq kirayə {price:g} < {RENT_MIN_TOTAL}")
        elif per_m2 is not None and per_m2 < RENT_MIN_PER_M2:
            flags.append(f"kirayə {per_m2:.1f} AZN/m² < {RENT_MIN_PER_M2}")
    elif deal == "sale":
        if price < SALE_MIN_TOTAL:
            flags.append(f"satış {price:g} < {SALE_MIN_TOTAL}")
        elif per_m2 is not None and per_m2 < SALE_MIN_PER_M2:
            flags.append(f"satış {per_m2:.0f} AZN/m² < {SALE_MIN_PER_M2}")
    return flags
