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
- Gün 4 BİTDİ: müştəri qəbul (inline təsdiq, JS confirm yox) / rədd / tələbi bağla (close_reason), qəbulda `contacts`
  → makler müştərinin adını, tel: və wa.me linkini görür (yalnız accepted + contacts); bildirişlər (yeni uyğun tələb
  ≥50, qəbul, rədd; `/kabinet/bildirisler`); müştəri "Yeni" nişanı (`customer_seen_at`); şikayətlər (hər iki tərəf,
  unikal indekslə 1 dəfə); tam admin panel; test bazası ayrıldı; startup advisory lock; debug IP səhifəsi silindi.
- Gün 5 BİTDİ: avtomatik təmizlik (`maintenance.py`), demo rejimi (`demo.py`, `is_demo`), təhlükəsizlik başlıqları,
  `/robots.txt`, 404/500 mətnləri, Sentry (`monitoring.py`), köhnə placeholder marşrut/şablonları silindi, README +
  `docs/screenshots/`. pytest: `tests/test_gun5.py`.
- Paket A (8 noyabr): admin əlaqəsi settings-də (`admin_contact_name/phone/email`, `notify_email` eyni e-poçta
  bağlı; admin Ayarlar → "Admin əlaqəsi"), `contact.py` (`admin_contact` tənbəl, sorğu başına 1 oxu), kart
  `templates/_admin_contact.html` (`contact_card`, ictimai səhifələr üçün `obf_email/obf_phone` + `js/contact-reveal.js`
  — HTML-də açıq e-poçt/telefon YOX). Kart: makler Ayarlar → Lisenziya, `auth/lisenziya.html`, `auth/cihaz.html`
  (cihaz limiti), kabinetdə ≤7 gün banner (qısa). Ana səhifədən Bina.az/Tap.az müqayisəsi və demo linki silindi.
  Daxil olmuş istifadəçi: ana səhifədə avatar + menyu (`css/pages/landing-user.css`, `js/user-menu.js`; Çıxış menyuda,
  POST), əsas düymə "Kabinetə keç →"/"Admin panel →"; `landing.js` `#registerBtn` gözlədiyi üçün gizli span qalır.
  /giris və /qeydiyyat daxil olmuş istifadəçiyə izah kartı (`auth/_signed_in.html`), "Çıxış et və davam et" →
  POST `/cixis?next=` (yalnız daxili ünvan; GET /cixis köhnə linklər üçün qalır). CSRF xətasında /giris və
  /qeydiyyat formanı dəyərlərlə qaytarır (`CSRF_SOFT_ENDPOINTS`, `CSRF_RETRY`). pytest: `tests/test_paket_a.py`.
- Şəkillər (8 noyabr): təklifə 1-5 şəkil Cloudinary-ə (`images.py`, `offer_images` cədvəli; `offers.image_url` yalnız
  köhnə təkliflər üçün, formada link sahəsi yoxdur). pytest: `tests/test_images.py`.
- IP (7 noyabr): Render-in qarşısında Cloudflare var, XFF 3 hop-dur (müştəri, Cloudflare, Render daxili).
  `auth.client_ip()` — IP lazım olan HƏR YERDƏ yalnız bu (birbaşa `request.remote_addr` yox). `MAKLERHUB_BEHIND_PROXY=1`
  olanda: CF-Connecting-IP → True-Client-IP → remote_addr (ipaddress ilə yoxlanır, etibarsız → növbəti); lokalda
  başlıqlara baxılmır. ProxyFix yalnız proto üçün (x_for=0, x_proto=1); waitress `clear_untrusted_proxy_headers=False`
  (proxy rejimində). IP ictimai deyilsə IP limitləri tətbiq olunmur (fail-open, `auth.ip_limit_applies`, bir dəfə
  warning): `/teleb` gündəlik 3 və lisenziya kodu 10/15 dəq. Nömrə limiti qalır. Testlər: `tests/test_client_ip.py`
  (bazasız, ayrıca: `pytest tests/test_client_ip.py -p no:cacheprovider`).
- Paket B (8 noyabr): rayon seçici `templates/_district_picker.html` (`district_multi`/`district_single`; JS-siz adi
  checkbox/select, eyni input adları — server qaydaları dəyişmir) + `js/district-picker.js`, `css/pages/district-picker.css`;
  siyahı `helpers.DISTRICTS_AZ` (Azərbaycan əlifbası, `az_sort_key`). İstifadə: `/teleb` (limitsiz + "Fərq etmir (bütün Bakı)" `any_name`), Ayarlar xidmət sahəsi
  (limitsiz, `data-district`), kabinet filtri (tək). Demo Ayarlar: düymələr görünür, `fieldset disabled class="ro-zone"`
  + `css/pages/readonly.css`, `js/readonly-tip.js`. Ana səhifə: hero-da tək düymə "Tələbini yaz →", maklerlər üçün
  `#maklerler` bölməsi (3 `mk-card`), `landing-anim.js/css`; WhatsApp linki `contact-reveal.js` ilə (`data-k="w"`).
  pytest: `tests/test_paket_b.py`.
- Bütün Bakı (8 noyabr): `/teleb`-də rayon limiti götürüldü; bütün `BAKU_DISTRICTS` seçilmiş tələb hər yerdə
  "Bütün Bakı" kimi göstərilir (`helpers.districts_text` filtri / `is_all_baku`; ayrıca sütun YOX). Uyğunluq dəyişmir.
  `static/js/pages/teleb.js`-ə toxunulmayıb: forma `data-max` = rayonların sayı.
- Paket C1 (8 noyabr): müştəri linki itməsin, xarici servis YOX.
  `js/teleb-memory.js`: /teleb/<token> (`[data-mh-save]`, yalnız aktiv tələb) tokeni localStorage `mh_requests`-ə yazır
  ({token, saved_at}, ≤5, ən yenisi əvvəldə; bağlanmış/bitmiş → silinir). Ana səhifə və /teleb-də `[data-mh-banner]`:
  yaddaşda token VARSA `POST /api/teleb/status` (yoxdursa sorğu yoxdur — baza oyanmır); "×" sessionStorage
  `mh_banner_hidden`. Ana səhifədə banner `.content`-in üstündə absolute (məzmunu itələmir), çoxlu tələb yığılır.
  `teleb.status_api`: CSRF YOX (`auth.CSRF_EXEMPT_ENDPOINTS`), Origin/Referer host yoxlanır (403), IP başına dəqiqədə
  20 (proses yaddaşı `_status_hits`, `ip_limit_applies` ilə), ≤5 token (çox → 400), tapılmayan → yalnız
  `{found:false}`, həmişə 2 SQL (ayar + bir toplu sorğu). Telefon/ad/token cavabda YOX.
  "Linki saxla" (`templates/teleb/_share.html`, `js/teleb-share.js`, `css/pages/teleb-share.css`): yenicə yaradılan
  tələbdə böyük blok yuxarıda (session `teleb_new` = request id, token cookie-yə yazılmır), sonra kiçik — təkliflərdən
  sonra. QR: `/teleb/<token>/qr.svg` (segno, serverdə). PWA: `/teleb/<token>/manifest.webmanifest` (start_url/scope =
  tələb səhifəsi), ikonlar `static/img/icon-192.png`, `icon-512.png`, `apple-touch-icon.png` (logo.png-dən, bir dəfəlik).
  Service worker YOXDUR. QR/manifest: `private, no-store`, noindex, səhv token 404. Sentry: `token/tokens/mh_requests`
  açarları maskalanır. pytest: `tests/test_paket_c1.py`.
- Açıq: dizayn istəkləri siyahısı (istifadəçi sonra bir dəfəyə göndərəcək).

## Qaydalar (pozma)
- `maklerhub/static/` içindəki MÖVCUD fayllara toxunma (CSS, JS, şəkillər, loqo). Lazım olsa YENİ fayl əlavə et
  (`static/css/pages/`, `static/js/pages/`), mövcud CSS-i `@import` edib dəyişənləri/sinifləri istifadə et.
  İSTİSNA (Gün 5, istifadəçinin icazəsi ilə): `static/js/app.js`-də yalnız bildiriş zənginin boş mətni dəyişdirilib
  ("Hələ bildiriş yoxdur. Yeni uyğun tələb gələndə burada görünəcək."); başqa heç nə.
- `templates/*/base.html`, `auth/_logo.html`, naviqasiya və ümumi layout saxlanılır (`makler/base.html`-ə yalnız
  `css/pages/makler-mobile.css` linki əlavə olunub — 375px-də ad ellipsis). Köhnə placeholder marşrut və şablonları
  (elanlar, axtarislar, musteriler, analitika, admin menbe, `_detail`, `_elan_cards`) Gün 5-də silindi; onların
  static faylları qalır (toxunma).
- `run.py`, `start.sh`, `start.bat` dəyişmir. İSTİSNA: `run.py` yalnız proxy konfiqurasiyası üçün dəyişdirilib
  (`MAKLERHUB_BEHIND_PROXY=1` → waitress `clear_untrusted_proxy_headers=False`); başqa dəyişiklik etmə.
- `db.py`-dakı uyğunlaşdırıcı qat (Row, Conn, Cursor, `_prepare`, pool, `get_db`) saxlanılır.
- git push ETMƏ — yalnız lokal commit (istifadəçi özü istəməsə).
- `init-db` bütün sxemi DROP edir: işlətməzdən əvvəl `.env`-dəki aktiv DATABASE_URL ilə şərhdəki köhnə URL-in host
  hissəsini müqayisə et (şifrəni ekrana çıxarma); eynidirsə DAYAN və istifadəçiyə de.
- Mühit dəyişənləri yalnız `.env`-də / Render-də: DATABASE_URL, MAKLERHUB_ADMIN_EMAIL, MAKLERHUB_ADMIN_PASSWORD,
  MAKLERHUB_SECRET, MAKLERHUB_SECURE_COOKIES, MAKLERHUB_BEHIND_PROXY (yalnız Render-də =1, lokalda yox),
  MAKLERHUB_DEMO_PASSWORD (yoxdursa "Demo12345"), SENTRY_DSN (yoxdursa Sentry söndürülüb), CLOUDINARY_CLOUD_NAME,
  CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET (yoxdursa şəkil yükləmə söndürülür; açarlar loqa/HTML-ə/Sentry-yə
  getmir — `monitoring.scrub_text` mühitdəki açar dəyərlərini maskalayır). BUNLARI HEÇ VAXT fayla yazma, çatda göstərmə,
  commit etmə.
- Demo izolyasiyası: makler tərəfində tələb seçən HƏR sorğu `is_demo`-nu maklerinki ilə müqayisə etməlidir
  (`offers.SAME_KIND`, `_detail_ctx`, `send_offer`); bildiriş `notify_new_request` yalnız eyni növə; admin
  göstəriciləri demo-suz (`/admin?demo=1` — ayrıca). Canlı bazada `seed-demo`-nu yalnız istifadəçi işlədir.
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
- `teleb.py` — `/teleb` (honeypot `website`, IP başına 24 saatda 3, nömrə başına 2 aktiv tələb, rayon limiti yoxdur (min 1; `all_districts=1` → hamısı), razılıq;
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
  Demo makler: `/api/me/*` və `/api/password` → 403, ayarlar səhifəsi oxu rejimi.
- `admin.py` — `/admin/lisenziyalar` (yaratma 1-20, ləğv), loglar (`/admin/api/logs`); `/admin/ayarlar` — şifrə +
  "Təmizliyi indi işlət" (`/admin/ayarlar/temizlik`), son təmizləmə vaxtı və saylar.
- `maintenance.py` — `run_cleanup` (oxunmuş bildiriş 30 gün, oxunmamış 90, login_attempts 7, logs 90, active=0
  sessiyalar 30 gün + 30 gün görünməyən demo sessiyaları); `after_request` → `response.call_on_close` (cavabdan sonra,
  ayrı bağlantı, yalnız bazaya toxunan sorğularda, saatda 1 dəfə — settings `last_cleanup_at` atomik UPSERT; xəta
  loqa). `lazy_expire(db)` — `expire_requests` dəqiqədə 1 dəfə (`last_expire_at`); birbaşa `expire_requests` çağırma.
  Testlərdə `CLEANUP_DISABLED=True`, conftest hər testdə tezlik məhdudiyyətini sıfırlayır.
- `demo.py` — `seed-demo` / `clear-demo` CLI; demo tələblərin müddəti bitmir (`request_alive`, `expire_requests`);
  demo makler cihaz limiti və tək sessiyadan azad, uğursuz giriş bloku tətbiq olunmur. Ana səhifədə demo linki və
  `/giris?demo=1` avtomatik doldurması YOXDUR (Paket A-da silindi); demo girişi yalnız README-də.
- `images.py` — Pillow: məzmuna görə format (JPEG/PNG/WEBP), ≤5 MB, 200..10000 px, `MAX_IMAGE_PIXELS` (bomb),
  `exif_transpose`, metadata silinir (yalnız ICC qalır), uzun tərəf 1600, JPEG q82. Cloudinary imzalı yükləmə
  (`maklerhub/offers/<offer_id>/<təsadüfi>`, overwrite=False), `destroy` (toplu, xəta loqa), `delete_orphans`,
  çatdırma URL-ləri (`CARD` w_800, `THUMB` 200×200 c_fill, `FULL` w_1600, hamısı f_auto,q_auto). Şəbəkə yalnız
  `_upload/_delete/_list`-dədir — conftest-də `cloud` autouse fixture onları BÜTÜN testlərdə mock edir.
  Axın (`makler.teklif_gonder`): yoxla/emal → `precheck_offer` (kilidsiz, rollback) → `reserve_offer_id` (sequence)
  → Cloudinary (açıq tranzaksiya YOX) → `send_offer` (qısa FOR UPDATE tranzaksiya + `offer_images`); uğursuzluqda
  yüklənənlər silinir. Limitlər: təklif başına 5 (demo 2), makler başına saatda 60. Geri çəkiləndə şəkillər qalır;
  admin silməsi, 90 gün anonimləşdirmə, `clear-demo` → Cloudinary-dən silinir; yetimlər həftədə 1 (`maintenance`).
  413 (forma > 26 MB) → `__init__.too_large`. Klient: `static/js/pages/offer-upload.js` (önizləmə, əsas et/sil,
  canvas 2000px, DataTransfer; JS-siz adi forma), `offer-gallery.js` (scroll-snap qalereya, lightbox ESC/geri),
  `css/pages/offer-images.css`.
- `monitoring.py` — Sentry yalnız `SENTRY_DSN` ilə; `before_send` forma/cookie/query/başlıqları silir, telefon,
  e-poçt, `/teleb/<token>` maskalanır; `include_local_variables=False`.
- `__init__.py` — `security_headers` (nosniff, DENY, Referrer-Policy setdefault, proxy-də HSTS), `/robots.txt`.
- `db.py` — Postgres üzərində sqlite-üslublu qat (`?` → `%s`, `INSERT OR IGNORE`, `lastrowid` — `_ID_TABLES`),
  `DEFAULT_SETTINGS`, `add_log`, `init-db` CLI. Hovuz `min_size=0` (Neon yata bilsin).
- `helpers.py` — vaxt (`now`, `ts`, `parse`, Asia/Baku), formatlama (`rel`, `date_short`, `money`, `initials`,
  `full_name`, `az_upper`), `BAKU_DISTRICTS` (12 rayon + populyar ərazilər).
- `seed.py` — `seed_minimal`: 1 admin + 3 istifadə olunmamış lisenziya kodu (konsola yazılır). Demo — `demo.py`.
- `schema.sql` — cədvəllərin mənbəyi. Yeni sütunlar YALNIZ `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (canlı bazada
  startup-da tətbiq olunur). Canlı bazada `init-db` İŞLƏTMƏ.
- `db.bootstrap()` — startup: lock + sxem + (boş bazada) seed BİR tranzaksiyada, `pg_advisory_xact_lock`
  (Neon -pooler/PgBouncer sessiya lock-unu saxlamır; xact lock commit/rollback-da buraxılır). `init_db` də lock altında.
- `offers.py` (Gün 4) — `decide_offer` (tələb FOR UPDATE, token mütləq təklifin tələbinə aid, qəbul limiti 3),
  `close_request`, `add_complaint`, `contacts_for` (telefon YALNIZ buradan), `notify_new_request`.
- `admin.py` — panel (göstəricilər, son 10 tələb/təklif), `/admin/telebler` (+ detal: telefon admin üçün, bağla, sil —
  cascade), `/admin/maklerler` (dayandır/aktivləşdir, cihazları sıfırla, lisenziyanı N gün uzat), `/admin/lisenziyalar`,
  `/admin/sikayetler`, `/admin/loglar`, `/admin/ayarlar` (şifrə). Admin hərəkətləri loqa yazılır.

## Cədvəllər
users (role admin|makler, status active|suspended, license_id, is_demo 0|1), licenses (code, days, status new|used|revoked,
user_id, activated_at, expires_at), devices, sessions, login_attempts, settings, logs, agent_areas (makler ərazisi:
district, deal_type rent|sale, price_min/max), customer_requests (token, phone, deal_type, districts JSON mətn,
rooms 5=5+, budget, urgency urgent|month|flexible, consent, status active|closed|expired, is_demo 0|1), offers (UNIQUE request_id+
agent_id, status sent|accepted|rejected|withdrawn), contacts (offer_id UNIQUE), notifications (user_id və ya
request_id, is_read), complaints (from_side customer|agent, status new|reviewed).
Vaxtlar TEXT "YYYY-MM-DD HH:MM:SS" (Asia/Baku, `helpers.ts()`).

## İşə salma
- Windows-da Python əmrlərini `PYTHONIOENCODING=utf-8` ilə işlət, yoxsa Azərbaycan hərfləri xəta verir.
- `python run.py [--debug] [--port N]` — server (waitress; `--debug` Flask serveri).
- Testlər: `PYTHONIOENCODING=utf-8 python -m pytest tests -q -p no:cacheprovider` (~7 dəq; pytest
  `requirements-dev.txt`-dədir). YALNIZ `.env`-dəki TEST_DATABASE_URL-də işləyir (`tests/dbguard.py`): yoxdursa və ya
  host-u DATABASE_URL ilə eynidirsə pytest dərhal dayanır; sessiyanın əvvəlində test bazası `init_db` ilə sıfırlanır;
  prosesdə DATABASE_URL test bazası ilə əvəz olunur. Fixture-ları skriptdən çağırmaq lazımdırsa əvvəl
  `conftest.pytest_configure(None)` (yoxsa `app` fixture dayanır). Lokal server test bazası ilə:
  `DATABASE_URL=<test url> python run.py` (yoxsa startup canlı bazaya sxem tətbiq edir).
- `flask --app run init-db --yes [--demo]` — bazanı SIFIRDAN yaradır (hər şey silinir), admin və kodları konsola yazır.
  Boş bazada sxem və `seed_minimal` ilk işə salınmada avtomatik yaradılır.
- Render (pulsuz): 15 dəq boş qalanda yatır, ayda 750 saat; Neon pulsuz limiti.

## Təhlükəsizlik
Admin şifrəsi `.env`-dədir, real pilotdan əvvəl dəyişdirilməlidir. Sessiya + CSRF (formlarda `csrf_token`, API-də
`X-CSRF-Token`); 15 dəqiqədə `login_fail_limit` uğursuz cəhddən sonra blok.
