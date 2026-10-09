# MaklerHub — işlək platforma

Daşınmaz əmlak maklerləri üçün elan izləmə və müştəri-elan uyğunlaşdırma platforması.
Bütün səhifələr göndərdiyiniz dizayn fayllarının **eyni HTML/CSS/animasiyaları** ilə qurulub —
fərq ondadır ki, indi arxada real server (Python/Flask), verilənlər bazası (SQLite),
giriş sistemi və işlək API-lər var.

## 1. İşə salmaq (3 addım)

Kompüterdə **Python 3.9+** olmalıdır.

**Windows:** `start.bat` faylına iki dəfə klikləyin.
**macOS / Linux:** terminalda `./start.sh`

və ya əl ilə:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Brauzerdə açın: **http://127.0.0.1:5000**

İlk işə salınmada `instance/maklerhub.db` bazası avtomatik yaradılır və dizaynlardakı
nümunə məlumatlarla doldurulur.

Giriş hesabları `.env` faylındakı (və ya Render Environment-dəki) `MAKLERHUB_ADMIN_*` və
`MAKLERHUB_DEMO_*` dəyişənləri ilə yaradılır. Şifrələr kodda saxlanmır.

## 2. Səhifələr

| Ünvan | Dizayn faylı | Nə işləyir |
|-------|--------------|------------|
| `/` | Landing Page | Giriş/Qeydiyyat düymələri (şəkil keçidi + qaralma animasiyası ilə) səhifələrə aparır |
| `/giris` | Giriş | Real giriş, "Məni xatırla" (30 gün), uğursuz cəhd limiti, şifrə bərpası |
| `/qeydiyyat` | Qeydiyyat | Alıcı / Satıcı / Makler; makler üçün lisenziya, sınaq müddəti avtomatik başlayır |
| `/kabinet` | Kabinet | Bugünkü ən uyğun elanlar, statistika, aktiv profillər; karta klik → elanın detalları |
| `/axtarislar` | Axtarışlar | Profil yarat / redaktə et / dayandır / sil; yaradılan kimi uyğun elanlar tapılır |
| `/elanlar` | Elanlar | Mənbə filtri, sıralama, "Daha çox göstər", qiymət düşməsi, detallar + WhatsApp ilə göndər |
| `/musteriler` | Müştərilər | Əlavə et, redaktə, "sövdə bağlandı", arxiv, sil, axtarış və filtr |
| `/analitika` | Analitika | Son 7/30/90 gün göstəriciləri, 14 günlük qrafik, mənbə bölgüsü, reytinq |
| `/ayarlar` | Ayarlar | Profil, avatar yükləmə, bildirişlər, mənbə seçimi, şifrə dəyişmə, plan seçimi |
| `/admin` | Admin Panel | Canlı statistika, mənbə statusu, son maklerlər, son loqlar |
| `/admin/maklerler` | Admin · Maklerlər | Makler əlavə et, dayandır/aktivləşdir, plan dəyiş, sınağı uzat, şifrə sıfırla, sil |
| `/admin/menbe` | Admin · Mənbə statusu | Uptime, cavab vaxtı, xətalar, 14 günlük qrafik; "İndi sinxronlaşdır", tənzimləmələr |
| `/admin/loglar` | Admin · Sistem loqları | Bazadan canlı loqlar, filtr, axtarış, səhifələmə, CSV yükləmə |
| `/admin/ayarlar` | Admin · Ayarlar | Bütün parametrlər bazada saxlanır və sistemə təsir edir |

## 3. Necə işləyir

- **Uyğunlaşdırma mühərriki** (`maklerhub/matching.py`) — hər elanı hər aktiv axtarış profili ilə
  müqayisə edib 0–100 bal verir (rayon, büdcə, otaq sayı, tikili tipi, təmir, metro, mərtəbə) və
  "✓ Rayon uyğundur · △ Metro məsafəsi bir az uzaq" kimi səbəbləri yazır.
- **Mənbə sinxronizasiyası** (`maklerhub/sources.py`) — fon axını hər *N* dəqiqədən bir
  (admin ayarlarındakı "Yoxlama intervalı") Bina.az, Tap.az və Lalafo adapterlərini işə salır,
  yeni elanları bazaya yazır, qiymət dəyişikliklərini izləyir, uyğunluqları hesablayır, loq yaradır.
  Yavaş mənbədə sorğu limiti avtomatik azaldılır, uğursuz sorğular təkrar cəhd olunur.
- **Təhlükəsizlik** — şifrələr hash-lənir (Werkzeug scrypt), bütün POST sorğularında CSRF qoruması,
  admin sessiyası passivlikdə bitir, uğursuz giriş limiti aşıldıqda 15 dəq blok.

### Real elan mənbəyinə qoşulmaq

Standart olaraq `DemoAdapter` işləyir — real bazar qiymətlərinə yaxın nümunə elanlar yaradır ki,
platforma dərhal canlı görünsün. Bina.az / Tap.az / Lalafo-nun rəsmi API-si və ya partnyor feed-i
(XML/JSON) əldə edildikdə `sources.py`-da `BaseAdapter`-dən sinif yaradıb `fetch()` metodunu yazmaq
və `ADAPTERS` lüğətində qeyd etmək kifayətdir. Qalan hər şey (baza, uyğunlaşdırma, admin statistikası,
loqlar) dəyişmədən işləyəcək. Saytları icazəsiz "scrape" etmək onların istifadə şərtlərinə zidd ola
bilər — rəsmi razılaşma tövsiyə olunur.

## 4. Konfiqurasiya (mühit dəyişənləri)

| Dəyişən | Standart | Təsvir |
|---------|----------|--------|
| `MAKLERHUB_DB` | `instance/maklerhub.db` | Baza faylı |
| `MAKLERHUB_SECRET` | avtomatik | Sessiya açarı (istehsalda mütləq təyin edin) |
| `MAKLERHUB_TZ` | `Asia/Baku` | Saat qurşağı |
| `MAKLERHUB_SCHEDULER` | `1` | Fon sinxronizasiyası (0 = söndür) |
| `MAKLERHUB_MAIL_SERVER`, `_PORT`, `_USERNAME`, `_PASSWORD`, `_FROM` | — | Şifrə bərpası e-poçtu üçün SMTP. Qurulmayıbsa link server konsoluna yazılır |
| `MAKLERHUB_SECURE_COOKIES` | `0` | HTTPS arxasında `1` edin |

## 5. Faydalı əmrlər

```bash
python run.py --port 8080            # başqa port
python run.py --host 0.0.0.0         # yerli şəbəkədəki digər cihazlar üçün
flask --app maklerhub init-db        # bazanı sıfırla (nümunə məlumatlarla)
flask --app maklerhub init-db --empty   # boş baza (yalnız admin hesabı)
flask --app maklerhub sync           # mənbələri dərhal sinxronlaşdır
```

## 6. Layihə strukturu

```
MaklerHub/
├── run.py                  # serveri işə salır (waitress, yoxdursa Flask)
├── requirements.txt
├── start.bat / start.sh    # bir kliklə quraşdırma + işə salma
├── instance/               # baza, sessiya açarı, yüklənmiş avatarlar (avtomatik)
└── maklerhub/
    ├── __init__.py         # tətbiq fabriki
    ├── config.py
    ├── schema.sql          # baza sxemi
    ├── db.py               # baza, ayarlar, loqlar
    ├── seed.py             # nümunə məlumatlar
    ├── helpers.py          # Azərbaycan dilində tarix/qiymət formatları
    ├── auth.py             # giriş, qeydiyyat, CSRF, şifrə bərpası
    ├── makler.py           # makler kabineti + API
    ├── admin.py            # admin panel + API
    ├── matching.py         # uyğunlaşdırma mühərriki
    ├── sources.py          # mənbə adapterləri + fon sinxronizasiyası
    ├── templates/          # Jinja şablonları (dizaynlarla eyni HTML)
    └── static/
        ├── css/pages/      # hər səhifənin orijinal CSS-i (dəyişdirilməyib)
        ├── css/app-*.css   # modal, menyu, bildiriş komponentləri (eyni dizayn dilində)
        ├── js/             # ümumi JS + hər səhifənin skripti
        └── img/            # loqo, mənbə ikonları, fon şəkilləri
```

## 7. İstehsala (serverə) yerləşdirmək

1. `MAKLERHUB_SECRET` təyin edin, `MAKLERHUB_SECURE_COOKIES=1` edin.
2. `python run.py --host 0.0.0.0 --port 8000` (waitress ilə) və qarşısına Nginx/Caddy (HTTPS) qoyun.
3. Boş bazadan başlamaq üçün: `flask --app maklerhub init-db --empty`, sonra admin şifrəsini dəyişin.
