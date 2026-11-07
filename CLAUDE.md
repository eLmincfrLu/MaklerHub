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
- Gün 2: makler qeydiyyatı lisenziya kodu ilə. Sonrakı: tələb forması, təkliflər, kontakt açılması, admin (lisenziyalar,
  şikayətlər). Gün 5: demo məlumatlar (`seed()` hələlik `seed_minimal` ilə eynidir).
- Açıq: dizayn istəkləri siyahısı (istifadəçi sonra bir dəfəyə göndərəcək); landing/giriş mətnlərində köhnə model
  ifadələri (Bina.az/Tap.az, "elan izləyir") hələ qalır.

## Qaydalar (pozma)
- `maklerhub/static/` içində heç nəyə toxunma (CSS, JS, şəkillər, loqo).
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

## Struktur
- `maklerhub/__init__.py` — `create_app`, public blueprint (`/`, `/saglamliq` — bazaya toxunmur, UptimeRobot üçün).
- `auth.py` — giriş/çıxış, şifrə bərpası (SMTP yoxdursa link konsola), CSRF, `login_required` / `member_required` /
  `admin_required`; `/qeydiyyat` hələlik placeholder. ROLES: admin, makler.
- `makler.py` — kabinet (`/kabinet`, `/ayarlar`) + bildiriş API (`/api/notifications`, zəng ikonu üçün).
  `/axtarislar`, `/elanlar`, `/musteriler`, `/analitika` — köhnə fayllar, placeholder, naviqasiyada yoxdur.
- `admin.py` — `/admin/` panel, maklerlər, ayarlar (placeholder), loglar (işləyir, `/admin/api/logs`); `/admin/menbe` placeholder.
- `db.py` — Postgres üzərində sqlite-üslublu qat (`?` → `%s`, `INSERT OR IGNORE`, `lastrowid` — `_ID_TABLES`),
  `DEFAULT_SETTINGS`, `add_log`, `init-db` CLI. Hovuz `min_size=0` (Neon yata bilsin).
- `helpers.py` — vaxt (`now`, `ts`, `parse`, Asia/Baku), formatlama (`rel`, `date_short`, `money`, `initials`,
  `full_name`, `az_upper`), `BAKU_DISTRICTS` (12 rayon + populyar ərazilər).
- `seed.py` — `seed_minimal`: 1 admin + 3 istifadə olunmamış lisenziya kodu (konsola yazılır); `create_licenses()`.
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
- `flask --app run init-db --yes [--demo]` — bazanı SIFIRDAN yaradır (hər şey silinir), admin və kodları konsola yazır.
  Boş bazada sxem və `seed_minimal` ilk işə salınmada avtomatik yaradılır.
- Render (pulsuz): 15 dəq boş qalanda yatır, ayda 750 saat; Neon pulsuz limiti.

## Təhlükəsizlik
Admin şifrəsi `.env`-dədir, real pilotdan əvvəl dəyişdirilməlidir. Sessiya + CSRF (formlarda `csrf_token`, API-də
`X-CSRF-Token`); 15 dəqiqədə `login_fail_limit` uğursuz cəhddən sonra blok.
