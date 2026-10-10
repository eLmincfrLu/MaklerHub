import json
import time
import requests

BASE = "https://bina.az/graphql"
HASH = "b781511a943a4d710eefdf811a24dd4ae353e55d836952603ce0b37fde97d073"

S = requests.Session()
S.headers.update({
    "accept": "*/*",
    "accept-language": "az-AZ,az;q=0.9,en-US;q=0.4,en;q=0.3",
    "content-type": "application/json",
    "referer": "https://bina.az/kiraye",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "x-platform": "desktop",
})


def sorgu(variables):
    params = {
        "operationName": "SearchItems",
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": HASH}}, separators=(",", ":")),
    }
    return S.get(BASE, params=params, timeout=30)


def goster(basliq, variables):
    print("\n===", basliq)
    r = sorgu(variables)
    print("   status:", r.status_code)
    if r.status_code != 200:
        print("   cavab:", r.text[:200])
        raise SystemExit("Dayandi: 200 deyil.")
    conn = r.json()["data"]["itemsConnection"]
    edges = conn["edges"]
    print("   totalCount:", conn["totalCount"], "| elan sayi:", len(edges), "| hasNext:", conn["pageInfo"]["hasNextPage"])
    for e in edges[:3]:
        n = e["node"]
        loc = (n.get("location") or {}).get("fullName")
        print("     ", n["id"], "| otaq:", n["rooms"], "| sahe:", (n.get("area") or {}).get("value"),
              "| qiymet:", n["price"]["total"], "| gunluk:", n["isPaidDaily"], "|", loc, "|", n["city"]["name"])
    return conn


base = {"first": 16, "filter": {"leased": True}, "sort": "BUMPED_AT_DESC"}

conn = goster("1. Birinci sehife", base)
time.sleep(4)

v = dict(base)
v["after"] = conn["pageInfo"]["endCursor"]
goster("2. Ikinci sehife (after)", v)