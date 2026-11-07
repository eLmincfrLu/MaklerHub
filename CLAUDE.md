# MaklerHub

Bakıda daşınmaz əmlak üçün Flask platforması. İstifadəçi azərbaycandilidir: cavabları Azərbaycan dilində, sadə və qısa
yaz. VS Code + Git Bash, Windows. İstifadəçi addım-addım izah istəyir ("mərhələ-mərhələ"); yoxlamaları özün et, ona etdirmə.

## Model (noyabr 2026-dan, köhnə scraper + elan uyğunlaşdırma modeli DAYANDIRILIB və silinib)
1. Müştəri saytda tələb yazır (kirayə/alış, rayon(lar), otaq, büdcə, təcililik) → `customer_requests`, gizli link `token`.
2. Maklerlər kabinetdə uyğun tələbləri görür və təklif göndərir → `offers` (bir tələbə bir makler — bir təklif).
3. Müştəri təklifi qəbul edəndə onun nömrəsi YALNIZ həmin maklerə açılır → `contacts`.
4. Makler admin verdiyi lisenziya kodu ilə qeydiyyatdan keçir → `licenses` (MH-XXXX-XXXX-XXXX, `days` müddət).

## Plan və vəziyyət
- Gün 1 BİTDİ: köhnə məntiq silindi, yeni sxem (13 cədvəl), sayt işə düşür; kabinet/panel "Tezliklə" placeholder.
- Gün 2 BİTDİ: lisenziya kodu ilə qeydiyyat, admin "Lisenziyalar" bölməsi, cihaz limiti + tək sessiya, makler xidmət
  sahəsi (Ayarlar), müştəri tələb forması `/teleb` və "Mənim tələbim" `/teleb/<token>`, landing yeni modelə keçdi.
- Gün 3 BİTDİ: ProxyFix + `client_ip()`, `/teleb` CSRF xətası formanı saxlayır, `/sertler` və `/mexfilik`, tələb
  müddəti (lazy), `matching.py`, kabinet tələb siyahısı (filtr, sıralama, 20-lik səhifə), tələb detalı + təklif,
  geri çəkmə, Təkliflərim, müştəri səhifəsində təkliflər. pytest: `tests/` (25 test).
- Sonrakı (Gün 4): müştəri qəbul/rədd, kontakt açılması (`contacts`), müştəri bildirişlərinin göstərilməsi, tam admin
  panel (cihaz sıfırlama UI, şikayətlər). Gün 5: demo məlumatlar (`seed()` hələlik `seed_minimal` ilə eynidir).
- Açıq: dizayn istəkləri siyahısı (istifadəçi sonra bir dəfəyə göndərəcək); ProxyFix x_for=1 Render-də bir proxy
  fərziyyəsinə əsaslanır — deploydan sonra real IP-nin gəldiyini loqda yoxla.

## Qaydalar (pozma)
- `maklerhub/static/` içindəki MÖVCUD fayllara toxunma (CSS, JS, şəkillər, loqo). Lazım olsa YENİ fayl əlavə et
  (`static/css/pages/`, `static/js/pages/`), mövcud CSS-i `@import` edib dəyişənləri/sinifləri istifadə et.
- `templates/*/base.html`, `auth/_logo.html`, naviqasiya və ümumi layout saxlanılır. Köhnə səhifə fayllarını silmə —
  içi "Tezliklə" placeholder-dir; struktur və CSS sinifləri qalır (`head`, `section`, `mh-empty`, `reveal`).
- `run.py`, `start.sh`, `start.bat` dəyişmir.
- `db.py`-dakı uyğunlaşdırıcı qat (Row, Conn, Cursor, `_prepare`, pool, `get_db`) saxlanılır.
- git push ETMƏ — yalnız lokal commit (istifadəçi özü istəməsə).
- `init-db` bütün sxemi DROP edir: işlətməzdən əvvəl `.env`-dəki aktiv DATABASE_URL ilə şərhdəki köhnə URL-in host
  hissəsini müqayisə et (şifrəni ekrana çıxarma); eynidirsə DAYAN və istifadəçiyə de.
- Mühit dəyişənləri yalnız `.env`-də / Render-də: DATABASE_URL, MAKLERHUB_ADMIN_EMAIL, MAKLERHUB_ADMIN_PASSWORD,
  MAKLERHUB_SECRET, MAKLERHUB_SECURE_COOKIES. BUNLARI HEÇ VAXT fayla yazma, çatda göstərmə, commit etmə.
- Müştəri nömrəsi heç vaxt kontakt (`contacts`) olmadan maklerə göstərilməməlidir.
- Telefonlar `+994XXXXXXXXX` saxlanılır (`helpers.normalize_phone`, operatorlar 10/50/51/55/60/70/77/99).

## Struktur
- `maklerhub/__init__.py` — `create_app` (ProxyFix x_for=1, x_proto=1), public blueprint (`/`, `/sertler`,
  `/mexfilik` — əlaqə e-poçtu `settings.notify_email`; `/saglamliq` — bazaya toxunmur, UptimeRobot üçün).
- `auth.py` — giriş/çıxış, şifrə bərpası (SMTP yoxdursa link konsola), CSRF, dekoratorlar; `/qeydiyyat` lisenziya
  kodu ilə (bir tranzaksiya, `FOR UPDATE`; IP başına 15 dəq-də 10 səhv kod → blok, `login_attempts.email='#license'`).
  Makler: `mh_device` cookie (httponly, 1 il; bazada `sha256(user_id:cookie)`), limit `settings.max_devices`=2,
  hər girişdə yeni `sessions` sətri, köhnələri `active=0`; session["st"] hər sorğuda yoxlanır. Lisenziya bitib/ləğv →
  `auth/lisenziya.html` (makler blueprint-i və /api/password bağlı). Admin: köhnə timeout, cihaz limiti yoxdur.
- `licenses.py` — kod yaratma/normallaşdırma (MH-XXXX-XXXX-XXXX), `activate`, `revoke`, `license_problem`,
  `reset_devices` (admin UI Gün 4-də).
- `teleb.py` — `/teleb` (honeypot `website`, IP başına 24 saatda 3, nömrə başına 2 aktiv tələb, 1-3 rayon, razılıq;
  CSRF uğursuzsa `g.csrf_failed` → forma dəyərlərlə yenidən), `/teleb/<token>` (noindex, Referrer-Policy: no-referrer,
  təkliflər — geri çəkilənlər yox, makler "Ad S."). `expire_requests(db)`: lazy — `settings.request_ttl_days`=30-dan
  sonra 'expired'; bağlanan/bitən tələbin ad/telefon/qeyd/IP-si 90 gün sonra silinir. `request_summary` = telefonsuz.
- `matching.py` — təmiz funksiyalar: qapı (deal_type sahədə var), rayon 55 + büdcə 45 × kəsişmə nisbəti
  (budget_min yoxdursa [max×0.7, max]); `GOOD_SCORE`=50; səbəblər ("ok"|"partial"|"no", mətn).
- `offers.py` — təklif yoxlaması (başlıq 80, qiymət aralığı, rayon, otaq, sahə, yalnız https şəkil ≤500, təsvir 500),
  `send_offer` (tələb sətri `FOR UPDATE`, 5 aktiv təklif limiti, `UNIQUE(request_id, agent_id)` — geri çəkən yenidən
  göndərə bilməz, müştəri üçün `notifications` kind='new_offer'), `load_ranked` (bir sorğu, N+1 yox), `apply_filters`.
- `makler.py` — menyu: Tələblər (`/kabinet`), Təkliflərim (`/kabinet/teklifler`), Ayarlar. `/kabinet/teleb/<id>`
  (detal + forma), `/kabinet/teleb/<id>/teklif`, `/kabinet/teklif/<id>/geri-cek`. Makler tərəfinə tələbin telefonu
  HEÇ VAXT seçilmir (`offers.REQ_COLS`), ad yalnız ilk ad. `/ayarlar`, API `/api/me/*`, `/api/notifications`.
  `/axtarislar`, `/elanlar`, `/musteriler`, `/analitika` — köhnə fayllar, placeholder, naviqasiyada yoxdur.
- `admin.py` — `/admin/lisenziyalar` (yaratma 1-20, ləğv), loglar (`/admin/api/logs`); panel, maklerlər, ayarlar,
  `/admin/menbe` — placeholder.
- `db.py` — Postgres üzərində sqlite-üslublu qat (`?` → `%s`, `INSERT OR IGNORE`, `lastrowid` — `_ID_TABLES`),
  `DEFAULT_SETTINGS`, `add_log`, `init-db` CLI. Hovuz `min_size=0` (Neon yata bilsin).
- `helpers.py` — vaxt (`now`, `ts`, `parse`, Asia/Baku), formatlama (`rel`, `date_short`, `money`, `initials`,
  `full_name`, `az_upper`), `BAKU_DISTRICTS` (12 rayon + populyar ərazilər).
- `seed.py` — `seed_minimal`: 1 admin + 3 istifadə olunmamış lisenziya kodu (konsola yazılır).
- `schema.sql` — cədvəllərin mənbəyi.

## Cədvəllər
users (role admin|makler, status active|suspended, license_id), licenses (code, days, status new|used|revoked,
user_id, activated_at, expires_at), devices, sessions, login_attempts, settings, logs, agent_areas (makler ərazisi:
district, deal_type rent|sale, price_min/max), customer_requests (token, phone, deal_type, districts JSON mətn,
rooms 5=5+, budget, urgency urgent|month|flexible, consent, status active|closed|expired), offers (UNIQUE request_id+
agent_id, status sent|accepted|rejected|withdrawn), contacts (offer_id UNIQUE), notifications (user_id və ya
request_id, is_read), complaints (from_side customer|agent, status new|reviewed).
Vaxtlar TEXT "YYYY-MM-DD HH:MM:SS" (Asia/Baku, `helpers.ts()`).

## İşə salma
- Windows-da Python əmrlərini `PYTHONIOENCODING=utf-8` ilə işlət, yoxsa Azərbaycan hərfləri xəta verir.
- `python run.py [--debug] [--port N]` — server (waitress; `--debug` Flask serveri).
- Testlər: `PYTHONIOENCODING=utf-8 python -m pytest tests -q` (pytest `requirements-dev.txt`-dədir). Axın testləri
  .env-dəki bazada işləyir və sonda öz sətirlərini silir (id nişanı ilə); `test_matching.py` bazasızdır.
- `flask --app run init-db --yes [--demo]` — bazanı SIFIRDAN yaradır (hər şey silinir), admin və kodları konsola yazır.
  Boş bazada sxem və `seed_minimal` ilk işə salınmada avtomatik yaradılır.
- Render (pulsuz): 15 dəq boş qalanda yatır, ayda 750 saat; Neon pulsuz limiti.

## Təhlükəsizlik
Admin şifrəsi `.env`-dədir, real pilotdan əvvəl dəyişdirilməlidir. Sessiya + CSRF (formlarda `csrf_token`, API-də
`X-CSRF-Token`); 15 dəqiqədə `login_fail_limit` uğursuz cəhddən sonra blok.
