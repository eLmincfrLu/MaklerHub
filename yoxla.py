import re
import requests

LINKLER = {
    "kiraye": "https://bina.az/kiraye",
    "satis": "https://bina.az/alqi-satqi",
    "elan": "https://bina.az/items/6287559",
}
H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "az,en;q=0.8"}

for ad, url in LINKLER.items():
    try:
        r = requests.get(url, headers=H, timeout=30)
    except Exception as e:
        print(ad, "XETA:", e)
        continue
    t = r.text
    print(ad, "| status:", r.status_code, "| olcu:", len(t))
    print("   __NEXT_DATA__:", "__NEXT_DATA__" in t)
    print("   apollo:", "apolloState" in t or "__APOLLO_STATE__" in t)
    print("   bloklama:", bool(re.search(r"cloudflare|captcha|just a moment", t, re.I)))