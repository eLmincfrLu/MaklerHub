"""Bina.az: siyahı sorğusu (SearchItems) və vahid formata gətirmə.

Qaydalar (plana görə):
- Heç bir sahə uydurulmur. Məlumat yoxdursa None (NULL) qalır.
- Əməliyyat növü (kirayə/satış) səhifənin `isLeased` bayrağından götürülür, qiymətə görə təxmin edilmir.
- Sorğular arasında 3-5 saniyə fasilə. Bloklanma əlaməti (403/429/JSON olmayan cavab) görünəndə DAYANIR,
  yan keçməyə çalışmır.
- Satıcı adı (company) və təsvir saxlanmır.
"""
import json
import random
import time

import requests

from .common import BlockedError, ScrapeError, price_flags

BASE = "https://bina.az"
GRAPHQL = BASE + "/graphql"
HASH = "b781511a943a4d710eefdf811a24dd4ae353e55d836952603ce0b37fde97d073"
CITY_BAKU = "1"

# Bina.az-ın Bakı rayonları (id-lər səhifənin özündən götürülüb)
DISTRICTS = {
    "Abşeron": "197", "Binəqədi": "58", "Xətai": "16", "Xəzər": "15", "Qaradağ": "57",
    "Nərimanov": "10", "Nəsimi": "190", "Nizami": "11", "Pirallahı": "249", "Sabunçu": "13",
    "Səbail": "12", "Suraxanı": "14", "Yasamal": "56",
}
# Mənzil kateqoriyaları (brauzerin öz filtrindən): 2 = Yeni tikili, 3 = Köhnə tikili
CATEGORIES = {"new": ("2", "Yeni tikili"), "old": ("3", "Köhnə tikili")}

# Şübhəli qiymət hədləri və qaydası ortaq moduldadır (scraper/common.py)
BinaError = ScrapeError


class BinaClient:
    def __init__(self, delay=(3.0, 5.0)):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({
            "accept": "*/*",
            "accept-language": "az-AZ,az;q=0.9,en-US;q=0.4,en;q=0.3",
            "content-type": "application/json",
            "referer": BASE + "/kiraye",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"),
            "x-platform": "desktop",
        })
        self._last = 0.0
        self.requests = 0        # göndərilən sorğu sayı
        self.req_time = 0.0      # cavab gözləmə vaxtlarının cəmi (saniyə)

    def _wait(self):
        gap = random.uniform(*self.delay)
        left = self._last + gap - time.time()
        if left > 0:
            time.sleep(left)
        self._last = time.time()

    @staticmethod
    def _params(flt, after, first):
        variables = {"first": first, "filter": flt, "sort": "BUMPED_AT_DESC"}
        if after:
            variables["after"] = after
        return {
            "operationName": "SearchItems",
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": HASH}},
                                     separators=(",", ":")),
        }

    def raw(self, flt, first=16):
        """Bir xam sorğu (ping üçün): requests.Response, xəta atmır."""
        self._wait()
        t0 = time.time()
        r = self.s.get(GRAPHQL, params=self._params(flt, None, first), timeout=30)
        self.requests += 1
        self.req_time += time.time() - t0
        return r

    def search(self, flt, after=None, first=16):
        params = self._params(flt, after, first)
        self._wait()
        t0 = time.time()
        r = self.s.get(GRAPHQL, params=params, timeout=30)
        self.requests += 1
        self.req_time += time.time() - t0
        if r.status_code in (401, 403, 429):
            raise BlockedError(f"HTTP {r.status_code}: sayt sorğunu rədd etdi. Dayanılır.")
        if r.status_code >= 500:
            time.sleep(30)  # bir dəfə təkrar
            self._last = time.time()
            r = self.s.get(GRAPHQL, params=params, timeout=30)
        if r.status_code != 200:
            raise BinaError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError:
            raise BlockedError("Cavab JSON deyil (bloklama/captcha ola bilər). Dayanılır.")
        if data.get("errors"):
            raise BinaError("GraphQL xətası: " + str(data["errors"][0].get("message"))[:300])
        return data["data"]["itemsConnection"]

    def pages(self, flt, max_pages):
        """Səhifələri ardıcıl verir (hər biri: totalCount, elan siyahısı, növbəti səhifə varmı).

        Son verilən has_next False-dursa, axın sonuna qədər oxunub.
        """
        after = None
        for _ in range(max_pages):
            conn = self.search(flt, after=after)
            has_next = bool(conn["pageInfo"]["hasNextPage"])
            yield conn["totalCount"], [e["node"] for e in conn["edges"]], has_next
            if not has_next:
                return
            after = conn["pageInfo"]["endCursor"]


def normalize(node, district, property_type):
    """Bina.az elanını bizim vahid formata çevirir.

    district və property_type sorğunun filtrindən bəllidir (siyahıdakı `location` etibarlı rayon vermir).
    """
    deal = "rent" if node.get("isLeased") else "sale"
    area = (node.get("area") or {}).get("value")
    rooms = node.get("rooms")
    price_obj = node.get("price") or {}
    price = price_obj.get("total")
    currency = price_obj.get("currency")
    if deal == "sale":
        period = "total"
    else:
        period = "day" if node.get("isPaidDaily") else "month"

    loc = node.get("location") or {}
    full = (loc.get("fullName") or "").strip()
    metro = loc.get("name") if full.endswith(" m.") else None
    settlement = loc.get("name") if full.endswith(" q.") else None

    title = f"{rooms} otaqlı {property_type.lower()}" if rooms else property_type
    if area:
        title += f", {area:g} m²"

    flags = price_flags(deal, period, price, area, currency)
    prev = node.get("preview") or {}
    img = prev.get("f460x345") or prev.get("thumbnail")

    return {
        "source": "bina",
        "external_id": str(node["id"]),
        "url": BASE + (node.get("path") or f"/items/{node['id']}"),
        "title": title,
        "deal_type": deal,
        "property_type": property_type,
        "district": district,
        "district_src": "bina" if district else None,   # rayon sorğunun filtrindəndir
        "metro_name": metro,
        "settlement": settlement,
        "rooms": rooms,
        "area": area,
        "floor": node.get("floor"),
        "floors": node.get("floors"),
        "price": price,
        "currency": currency,
        "price_period": period,
        "renovation": None,  # siyahıdakı hasRepair etibarlı deyil (bütün elanlarda true), ona görə boş qalır
        "images": [img] if img else [],
        "suspicious": 1 if flags else 0,
        "flags": flags,
        "source_updated_at": node.get("updatedAt"),
    }