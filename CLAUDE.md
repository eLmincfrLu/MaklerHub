# MaklerHub

Bakıda daşınmaz əmlak makleri üçün Flask platforması. Elanları (bina.az, sonra tap.az; lalafo ən axırda/V2) scrape edir
və müştərilərin axtarış profilləri ilə uyğunlaşdırır (matching.py, 0-100 bal).
İstifadəçi azərbaycandilidir: cavabları Azərbaycan dilində, sadə və qısa yaz. VS Code + Git Bash, Windows.
İstifadəçi addım-addım izah istəyir ("mərhələ-mərhələ"); yoxlamaları özün et, ona etdirmə.

## Plan və vəziyyət
25 günlük plan: `docs/` qovluğundakı PDF-lərə və `25 gunluk plan` mətninə bax.
- Gün 1-7 BİTDİ: admin/demo hesab (şifrələr `.env`-dən), qovluq təmizliyi, SQLite -> Neon PostgreSQL, 18 cədvəl,
  Render-də deploy, UptimeRobot (`/saglamliq`, bazaya toxunmur).
- Gün 8-9 BİTDİ: `scraper/` paketi, Bina.az dry-run işləyir (bazaya yazmır).
- Gün 10 BİTDİ: `scraper/store.py` (elanlar Neon-a yazılır: yeni/qiymət dəyişikliyi/yoxa çıxan/geri qayıdan, sync_runs, logs,
  matching), DemoAdapter söküldü, testlər (`tests/test_removed.py`, ROLLBACK ilə).
- NÖVBƏTİ: Gün 11 = Tap.az (11-12). Sonra dublikatlar (13), telefon (14), avtomatik planlama (15).
- Açıq: şübhəli qiymət hədlərinin yekunlaşdırılması (aşağıya bax); dizayn istəkləri siyahısı (istifadəçi sonra bir dəfəyə göndərəcək).

## Texniki
- Flask blueprints (auth, makler, admin, public), waitress, session + CSRF, Jinja.
- `maklerhub/db.py`: Postgres üzərində sqlite-üslublu uyğunluq qatı (`?` -> `%s`, `INSERT OR IGNORE`, `lastrowid`).
  Vaxtlar TEXT "YYYY-MM-DD HH:MM:SS" (Asia/Baku). `schema.sql` = cədvəllərin mənbəyi.
- Əmrlər: `python run.py`, `flask init-db [--demo] [--yes]` (bütün məlumatı SİLİR), `flask sync`.
- Windows-da Python əmrlərini PYTHONIOENCODING=utf-8 ilə işlət (məsələn: PYTHONIOENCODING=utf-8 python -m scraper.run ...), yoxsa Azərbaycan hərfləri xəta verir.
- Render (pulsuz): 15 dəq boş qalanda yatır, ayda 750 saat limiti; Neon pulsuz limiti. Hovuz `min_size=0` (Neon yata bilsin).
- Mühit dəyişənləri yalnız `.env`-də / Render-də: DATABASE_URL, admin/demo hesab məlumatları, MAKLERHUB_SECRET,
  MAKLERHUB_SECURE_COOKIES. BUNLARI HEÇ VAXT fayla yazma, çatda göstərmə, commit etmə.

## Scraper (Bina.az)
- Səhifələrdə elan yoxdur; məlumat GraphQL persisted query `SearchItems`-dən gəlir (`GET https://bina.az/graphql`,
  sha256Hash `scraper/bina.py`-dədir). Başlıqlar vacibdir (`x-platform: desktop`, content-type, referer, sec-fetch-*), yoxsa HTTP 400.
- Kursor səhifələmə (16/səhifə), `sort: BUMPED_AT_DESC`. Filtr: cityId 1 (Bakı), categoryId 2 Yeni / 3 Köhnə tikili,
  `leased` true/false (kirayə/satış), `locationIds`. Rayon siyahıdakı `location`-dan yox, FİLTRDƏN götürülür.
- QAYDALAR: heç bir dəyər uydurma (yoxdursa NULL); sorğular arası 3-5 san; 401/403/429 və ya JSON olmayan cavabda DAYAN
  (BlockedError), yan keçməyə çalışma; satıcı adı və təsvir saxlanmır; telefon yalnız ≥70% uyğunluqda.
- `hasRepair` etibarsızdır (hamısında true) -> renovation = None.
- Əməliyyat növü `isLeased`-dən; kirayə dövrü `isPaidDaily` -> day, əks halda month; satış -> total.
- Şübhəli qiymət (`suspicious`): kirayə aylıq < 100 AZN və ya < 3 AZN/m²; satış < 10 000 AZN və ya < SALE_MIN_PER_M2.
  Real ölçmə: Yasamal satış 2100-5400 AZN/m² (median ~2900), kirayə median ~11 AZN/m². SALE_MIN_PER_M2 = 400 qoyulub;
  digər rayonlar (Qaradağ, Sabunçu, Suraxanı, Abşeron) üçün real rəqəmə baxıb yekunlaşdır.
- Tezlik: ən tez-tez yoxlama daha yaxşıdır (saytda ~70-90 yenilənən kirayə elanı/saat). Hər axının yalnız 1-ci səhifəsi
  (ən təzələr) hər 10-30 dəq; silinənləri tapmaq üçün gündə 1 tam sayğı. 52 axın (13 rayon x 2 kateqoriya x 2 əməliyyat) ~3,5 dəq.
  Neon yatmasın deyə çox sıx dövr pulsuz saat limitini keçə bilər — 15-ci gündə real ölçüb seç.
- Rayon id-ləri `scraper/bina.py` DISTRICTS-dədir. sources.py-də Abşeron və Pirallahı əlavə olunub; makler.py-dəki profil
  formasının rayon siyahısında hələ yoxdur.
- Çıxış: `python -m scraper.run bina [--selftest] [--deal rent|sale|both] [--category new|old|both] [--district AD|all] [--pages N]
  [--save] [--full]`. `--save` olmadan dry-run (`numuneler/bina_dry.json`, .gitignore-dadır). `--full` = tam sayğı (kirayə ~1,5 saat,
  satış ~4 saat); yoxa çıxan yalnız axın sonuna qədər oxunubsa (hasNextPage=false) VƏ elan 24 saatdan çox görünməyibsə işarələnir.
- store.py: günlük kirayə (`price_period=day`) və suspicious=1 saxlanır, amma matching-ə girmir; building = property_type;
  renovation '' (məlum deyil). Veb proses (admin düyməsi, `flask sync`, planlayıcı) heç bir elan yaratmır.

## Təhlükəsizlik
Hesab şifrələri .env-dədir, real pilotdan əvvəl dəyişdirilməlidir. Scraper məlumatı Neon-a lokal prosesdən yazır.
Lalafo-nun şərtləri avtomatik girişi qadağan edir; bina.az/tap.az şərtləri yoxlanmayıb. İstifadəçi riski qəbul edib (10-15 makler testi).