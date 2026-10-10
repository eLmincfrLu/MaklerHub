import os
import requests

SAHIFELER = {
    "bina_kiraye.html": "https://bina.az/kiraye",
    "bina_satis.html": "https://bina.az/alqi-satqi",
    "bina_elan.html": "https://bina.az/items/6287559",
}
H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "az,en;q=0.8"}

os.makedirs("numuneler", exist_ok=True)
for fayl, url in SAHIFELER.items():
    r = requests.get(url, headers=H, timeout=30)
    with open(os.path.join("numuneler", fayl), "w", encoding="utf-8") as f:
        f.write(r.text)
    print(fayl, "saxlandi, olcu:", len(r.text))