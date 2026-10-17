"""Tap.az: siyahı sorğusu (GraphQL GetAdSearch) və vahid formata gətirmə.

Qaydalar (Bina.az ilə eyni):
- Heç bir sahə uydurulmur. Məlumat yoxdursa None (NULL) qalır; rayon tapılmasa district = ''.
- Kirayə/satış, kirayə müddəti və tikili tipi sorğunun FİLTRİNDƏN götürülür.
- Otaq, sahə və yer adı başlıqdan oxunur ("2-otaqlı …, Nəsimi m., 65 m²"). Mərtəbə Tap.az-da yoxdur → None.
- Rayon: başlıqda "X r." varsa X (yalnız tanınan rayon adı, "N.Nərimanov" → Nərimanov), əks halda
  metro/qəsəbə → district_map (adlar Bina.az yazılışına gətirilir). Mənbə district_src: title | map | None.
- Qiymətlər AZN sayılır (Tap.az-da valyuta sahəsi yoxdur).
- Sorğular arasında 3-5 saniyə fasilə. 401/403/429 və ya JSON olmayan cavabda DAYANIR (təkrar cəhd yoxdur).
- Timeout (45 san), bağlantı xətası və ya HTTP 5xx → hər sorğu üçün 3 cəhd (5 və 15 san gözləmə).
- Satıcı / mağaza məlumatı və təsvir saxlanmır.
"""
import json
import logging
import random
import re
import time

import requests

from . import district_map
from .bina import DISTRICTS as KNOWN_DISTRICTS
from .common import BlockedError, ScrapeError, StuckError, price_flags

BASE = "https://tap.az"
GRAPHQL = BASE + "/graphql"
CATEGORY_ID = "Z2lkOi8vdGFwL0NhdGVnb3J5LzYzNQ"   # gid://tap/Category/635 = Mənzillər
REGION_BAKU = "Z2lkOi8vdGFwL1JlZ2lvbi80MjA"      # gid://tap/Region/420 = Bakı
PAGE_SIZE = 24
TIMEOUT = 45                 # san (bir sorğunun cavab gözləmə həddi)
RETRY_WAITS = (5, 15)        # təkrar cəhdlər arası gözləmə (san) → cəmi 3 cəhd

log = logging.getLogger(__name__)

# Filtr xüsusiyyətləri (saytın CategoryProperties sorğusundan)
P_DEAL, DEAL_SALE, DEAL_RENT = 740, "3722", "3724"
P_PERIOD, PERIOD_MONTH, PERIOD_DAY = 994, "38046", "38047"
P_BUILDING = 747
CATEGORIES = {"new": ("3849", "Yeni tikili"), "old": ("3850", "Köhnə tikili")}

QUERY = """query GetAdSearch($first: Int, $after: String, $source: SourceEnum!, $filters: AdFilterInput) {
  adSearch(source: $source, filters: $filters) {
    ads(first: $first, after: $after) {
      nodes { id title price updatedAt region path kinds legacyResourceId status
              photo { url } desktopPhoto: photo(source: DESKTOP) { url } }
      pageInfo { endCursor hasNextPage }
    }
  }
}"""


def stream_filter(deal, period, category):
    """deal: rent|sale, period: month|day|total, category: new|old → AdFilterInput."""
    coll = [{"legacyId": P_DEAL, "value": DEAL_RENT if deal == "rent" else DEAL_SALE}]
    if deal == "rent":
        coll.append({"legacyId": P_PERIOD, "value": PERIOD_DAY if period == "day" else PERIOD_MONTH})
    coll.append({"legacyId": P_BUILDING, "value": CATEGORIES[category][0]})
    return {"categoryId": CATEGORY_ID, "regionId": REGION_BAKU,
            "propertyOptions": {"collection": coll, "boolean": [], "range": [], "string": []}}


class TapClient:
    def __init__(self, delay=(3.0, 5.0)):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({
            "accept": "*/*",
            "accept-language": "az-AZ,az;q=0.9,en-US;q=0.4,en;q=0.3",
            "content-type": "application/json",
            "origin": BASE,
            "referer": BASE + "/elanlar/dasinmaz-emlak/menziller",
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"),
        })
        self._last = 0.0
        self.requests = 0
        self.req_time = 0.0

    def _wait(self):
        gap = random.uniform(*self.delay)
        left = self._last + gap - time.time()
        if left > 0:
            time.sleep(left)
        self._last = time.time()

    def _post(self, body):
        """Bir sorğu. Timeout / bağlantı xətası / HTTP 5xx → 3 cəhd (5 və 15 san gözləmə); sonuncu xəta qalxır
        (və ya 5xx cavabı qaytarılır). 401/403/429 və digər cavablar dərhal qaytarılır (search yoxlayır)."""
        for attempt, pause in enumerate(RETRY_WAITS + (None,), 1):
            self._wait()
            t0 = time.time()
            try:
                r = self.s.post(GRAPHQL, data=body, timeout=TIMEOUT)
            except (requests.Timeout, requests.ConnectionError) as e:
                reason = f"{type(e).__name__}: {e}"
                r = None
                if pause is None:
                    raise
            finally:
                self.requests += 1
                self.req_time += time.time() - t0
            if r is not None:
                if r.status_code < 500 or pause is None:
                    return r
                reason = f"HTTP {r.status_code}"
            log.warning("tap.az: %s-ci cəhd alınmadı (%s) — %s san sonra təkrar", attempt, reason[:200], pause)
            time.sleep(pause)

    @staticmethod
    def _body(flt, after, first=PAGE_SIZE):
        variables = {"first": first, "source": "DESKTOP", "filters": flt}
        if after:
            variables["after"] = after
        return json.dumps({"operationName": "GetAdSearch", "query": QUERY, "variables": variables})

    def raw(self, flt, first=PAGE_SIZE):
        """Bir xam sorğu (ping üçün): requests.Response, xəta atmır."""
        return self._post(self._body(flt, None, first))

    def search(self, flt, after=None):
        body = self._body(flt, after)
        r = self._post(body)
        if r.status_code in (401, 403, 429):
            raise BlockedError(f"HTTP {r.status_code}: sayt sorğunu rədd etdi. Dayanılır.")
        if r.status_code != 200:
            raise ScrapeError(f"HTTP {r.status_code}: {r.text[:200]}")
        if "json" not in (r.headers.get("content-type") or ""):
            raise BlockedError("Cavab JSON deyil (bloklama/captcha ola bilər). Dayanılır.")
        try:
            data = r.json()
        except ValueError:
            raise BlockedError("Cavab JSON deyil (bloklama/captcha ola bilər). Dayanılır.")
        if data.get("errors"):
            raise ScrapeError("GraphQL xətası: " + str(data["errors"][0].get("message"))[:300])
        return data["data"]["adSearch"]["ads"]

    def pages(self, flt, max_pages):
        """Səhifələri ardıcıl verir: (cəmi elan sayı — Tap.az vermir → None, elanlar, növbəti səhifə varmı)."""
        after = None
        for _ in range(max_pages):
            conn = self.search(flt, after=after)
            has_next = bool(conn["pageInfo"]["hasNextPage"])
            yield None, conn["nodes"], has_next
            if not has_next:
                return
            nxt = conn["pageInfo"]["endCursor"]
            if has_next and (not nxt or nxt == after):
                raise StuckError(f"endCursor dəyişmir ({str(nxt)[:40]})")
            after = nxt


# ---------------------------------------------------------------- başlıqdan oxuma
_ROOMS = re.compile(r"^(\d+)-otaqlı\b")
_AREA = re.compile(r"^(\d+(?:[.,]\d+)?) m²$")


def parse_title(title):
    """'2-otaqlı yeni tikili kirayə verilir, Nəsimi m., 65 m²' → (otaq, sahə, yer mətni). Tapılmasa None."""
    title = (title or "").strip()
    m = _ROOMS.match(title)
    rooms = int(m.group(1)) if m else None
    parts = [p.strip() for p in title.split(",")]
    area = None
    if len(parts) >= 2:
        a = _AREA.match(parts[-1])
        if a:
            area = float(a.group(1).replace(",", "."))
            parts = parts[:-1]
    location = ", ".join(parts[1:]) if len(parts) >= 2 else None
    return rooms, area, (location or None)


def parse_location(location):
    """Yer mətni → (metro, qəsəbə, rayon). Tanınmayan formada hamısı None."""
    if not location:
        return None, None, None
    loc = location.strip()
    # adlar Bina.az yazılışına gətirilir ("İçərişəhər" → "İçəri Şəhər"); tanınmasa olduğu kimi qalır
    if loc.endswith(" m."):
        name = loc[:-3].strip()
        return district_map.canonical(name, district_map.METRO_NAMES) or name, None, None
    for suf in (" qəs.", " q."):
        if loc.endswith(suf):
            name = loc[:-len(suf)].strip()
            return None, district_map.canonical(name, district_map.SETTLEMENT_NAMES) or name, None
    if loc.endswith(" r."):
        return None, None, district_map.canonical(loc[:-3], list(KNOWN_DISTRICTS))
    return None, None, None


def normalize(node, deal, period, property_type):
    """Tap.az elanını bizim vahid formata çevirir (scraper/bina.py normalize ilə eyni açarlar)."""
    rooms, area, location = parse_title(node.get("title"))
    metro, settlement, district = parse_location(location)
    src = "title" if district else None              # başlıqda "X r."
    if not district:
        district = district_map.district_for(metro=metro, settlement=settlement)
        src = "map" if district else None            # metro/qəsəbə xəritəsindən
    price = node.get("price")
    currency = "AZN"
    title = f"{rooms} otaqlı {property_type.lower()}" if rooms else property_type
    if area:
        title += f", {area:g} m²"
    flags = price_flags(deal, period, price, area, currency)
    img = (node.get("desktopPhoto") or {}).get("url") or (node.get("photo") or {}).get("url")
    return {
        "source": "tap",
        "external_id": str(node["legacyResourceId"]),
        "url": BASE + (node.get("path") or f"/elanlar/dasinmaz-emlak/menziller/{node['legacyResourceId']}"),
        "title": title,
        "deal_type": deal,
        "property_type": property_type,
        "district": district or "",
        "district_src": src,
        "metro_name": metro,
        "settlement": settlement,
        "location_text": location,
        "rooms": rooms,
        "area": area,
        "floor": None,
        "floors": None,
        "price": price,
        "currency": currency,
        "price_period": period,
        "renovation": None,
        "images": [img] if img else [],
        "suspicious": 1 if flags else 0,
        "flags": flags,
        "source_updated_at": node.get("updatedAt"),
    }
