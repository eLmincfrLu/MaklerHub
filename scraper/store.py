"""Scraper nəticələrini Neon (PostgreSQL) bazasına yazır.

Qaydalar (Gün 10 planı):
- Yeni elan → listings + price_history, sonra uyğunlaşdırma (günlük kirayə istisna).
- Görülən elan → last_seen_at yenilənir; qiymət dəyişibsə price_history-yə yeni sətir.
- Yoxa çıxan elan → yalnız axın sonuna qədər oxunubsa (hasNextPage=false) VƏ elan
  sayğı başlanğıcından ən azı 24 saat əvvəl görülübsə: active=0, removed_at=indi.
- Hər axından sonra commit olunur, ona görə bloklanma olsa da toplanan məlumat itmir.
- save_many(): toplu yazma (scraper/auto.py): mövcud elanlar bir sorğu ilə oxunur, dəyişməyənlərin last_seen_at-ı
  bir UPDATE ilə yenilənir, tam emal (qruplaşdırma, matching, bildiriş) yalnız yeni / dəyişmiş elanlar üçündür.
- gate(row) → (icazə, məlumat): bildiriş qapısı (notify_min_id qaydası, scraper/auto.py). Yeni və uyğun elan
  üçün qərar gate_events-ə yazılır.
"""
import json
import time
from collections import Counter
from datetime import timedelta

from maklerhub import dups, matching
from maklerhub.db import add_log
from maklerhub.helpers import now, ts

REMOVE_AFTER = timedelta(hours=24)
_MISSING = object()
OLD_COLS = "id, external_id, price, active, suspicious, district, district_src, metro_name, settlement, images"


class Store:
    def __init__(self, con, source_key="bina", notify=True, gate=None, started=None, t0=None):
        self.con = con
        self.notify = notify  # False (--quiet): matching işləyir, bildiriş yaranmır
        self.gate = gate      # None: qapı yoxdur (bütün uyğun yeni elanlar üçün bildiriş)
        self.gate_events = []
        self._places = None
        self.source = con.execute("SELECT * FROM sources WHERE key = ?", (source_key,)).fetchone()
        if not self.source:
            raise RuntimeError(f"sources cədvəlində '{source_key}' yoxdur")
        self.profiles = con.execute("SELECT * FROM profiles WHERE status = 'active'").fetchall()
        # dövrün başlanğıcı (auto.py toplama başlayanda verir → sync_runs.duration = toplama + yazma)
        self.started = started or now()
        self.t0 = t0 or time.time()
        self.stats = Counter()

    def attach(self, con):
        """Yeni bağlantı (nightly hər axını ayrıca bağlantı ilə yazır); aktiv profillər yenidən oxunur."""
        self.con = con
        self.profiles = con.execute("SELECT * FROM profiles WHERE status = 'active'").fetchall()

    # ------------------------------------------------------------ toplu
    def save_many(self, rows):
        """Bir neçə elanı yazır: dəyişməyənlər toplu, qalanları save() ilə. Nəticələrin sayını qaytarır."""
        con, sid = self.con, self.source["id"]
        result = Counter()
        olds = {o["external_id"]: o for o in con.execute(
            f"SELECT {OLD_COLS} FROM listings WHERE source_id = ? AND external_id = ANY(?)",
            (sid, [r["external_id"] for r in rows]))}
        seen = []
        for row in rows:
            old = olds.get(row["external_id"])
            if old is not None and self._unchanged(row, old):
                self.stats["checked"] += 1
                if row["suspicious"]:
                    self.stats["suspicious"] += 1
                seen.append(old["id"])
                result["seen"] += 1
            else:
                result[self.save(row, old=old)] += 1
        if seen:
            con.execute("UPDATE listings SET last_seen_at = ? WHERE id = ANY(?)", (ts(), seen))
        return result

    def _src(self, row):
        # rayonun mənbəyi; Bina.az-da rayon həmişə sorğunun filtrindəndir
        return row.get("district_src") or ("bina" if self.source["key"] == "bina" and row["district"] else None)

    def _place(self, row, old):
        """(rayon, rayon mənbəyi, metro, qəsəbə). Tap.az-da rayon xəritədən gəlir, xəritə yenilənəndə köhnə
        elanların rayonu da düzəlir; Bina nüsxəsindən götürülmüş rayon ('dup') boş rayonla silinmir —
        onu dups.refresh yenidən yoxlayır."""
        district, src = row["district"], self._src(row)
        if not district and old["district_src"] == "dup":
            district, src = old["district"], "dup"
        return district, src, row["metro_name"], row.get("settlement")

    def _unchanged(self, row, old):
        if row["price"] is None or row["price"] <= 0:
            return False
        return (old["active"] and old["price"] == int(round(row["price"])) and old["suspicious"] == row["suspicious"]
                and old["images"] == json.dumps(row["images"] or [], ensure_ascii=False)
                and self._place(row, old) == (old["district"], old["district_src"], old["metro_name"],
                                              old["settlement"]))

    # ------------------------------------------------------------ bir elan
    def save(self, row, old=_MISSING):
        """Bir normalizə olunmuş elanı yazır. Nəticə: new | updated | seen | skipped.

        old: save_many()-dən əvvəlcədən oxunmuş sətir (None = bazada yoxdur)."""
        con, sid = self.con, self.source["id"]
        self.stats["checked"] += 1
        if row["price"] is None or row["price"] <= 0:
            self.stats["skipped"] += 1
            return "skipped"
        if row["suspicious"]:
            self.stats["suspicious"] += 1
        price = int(round(row["price"]))
        images = json.dumps(row["images"] or [], ensure_ascii=False)
        stamp = ts()
        matchable = row["price_period"] != "day"
        src = self._src(row)
        if old is _MISSING:
            old = con.execute(f"SELECT {OLD_COLS} FROM listings WHERE source_id = ? AND external_id = ?",
                              (sid, row["external_id"])).fetchone()
        if old is None:
            cur = con.execute(
                """INSERT INTO listings(source_id, external_id, url, title, suffix, district, rooms, area, floor,
                       floors, price, price_period, deal_type, property_type, building, renovation, metro_name,
                       settlement, district_src, images, suspicious, active, posted_at, created_at, updated_at,
                       last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
                (sid, row["external_id"], row["url"], row["title"], "kirayə" if row["deal_type"] == "rent" else "",
                 row["district"], row["rooms"], row["area"], row["floor"], row["floors"], price,
                 row["price_period"], row["deal_type"], row["property_type"], row["property_type"], "",
                 row["metro_name"], row.get("settlement"), src, images, row["suspicious"],
                 stamp, stamp, stamp, stamp),
            )
            lid = cur.lastrowid
            con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?, ?, ?)",
                        (lid, price, stamp))
            self.stats["new"] += 1
            self._group_and_match(lid, matchable, row=row, first_seen=True)
            return "new"

        lid = old["id"]
        price_changed = old["price"] != price
        reactivated = not old["active"]
        suspicious_changed = old["suspicious"] != row["suspicious"]
        con.execute("UPDATE listings SET last_seen_at = ?, images = ?, suspicious = ? WHERE id = ?",
                    (stamp, images, row["suspicious"], lid))
        place = self._place(row, old)
        district_changed = old["district"] != place[0]
        if place != (old["district"], old["district_src"], old["metro_name"], old["settlement"]):
            con.execute("UPDATE listings SET district = ?, district_src = ?, metro_name = ?, settlement = ? "
                        "WHERE id = ?", place + (lid,))
            if district_changed:
                self.stats["district_changed"] += 1
        if price_changed:
            con.execute("UPDATE listings SET price = ?, updated_at = ? WHERE id = ?", (price, stamp, lid))
            con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?, ?, ?)",
                        (lid, price, stamp))
        if reactivated:
            con.execute("UPDATE listings SET active = 1, removed_at = NULL, updated_at = ? WHERE id = ?",
                        (stamp, lid))
            self.stats["reactivated"] += 1
        if not (price_changed or reactivated or suspicious_changed or district_changed):
            return "seen"
        self.stats["updated"] += 1
        self._group_and_match(lid, matchable, row=row)
        return "updated"

    def _allowed(self, row):
        """Bildiriş qapısı: (icazə, məlumat)."""
        if self.gate is None or row is None:
            return True, None
        return self.gate(row)

    def _group_and_match(self, listing_id, matchable, row=None, first_seen=False):
        """Əvvəl təkrar qrupları yenilənir (dups.py), sonra elan və qrupu dəyişən qonşuları uyğunlaşdırılır."""
        con = self.con
        if self._places is None:
            self._places = dups.bina_places(con)
        listing = con.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        changed = dups.refresh(con, listing, self._places)
        self.stats["dup_changed"] += len(changed - {listing_id})
        if not matchable or not self.profiles:
            return
        done = set()
        for lid in [listing_id] + sorted(changed - {listing_id}):
            r = con.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
            key = ("g", r["dup_group"]) if r["dup_group"] is not None else ("l", lid)
            if key in done:
                continue
            done.add(key)
            # bildiriş yalnız gələn elanın özü üçün (və qapıdan keçirsə); qrupu dəyişən köhnə qonşular bildirişsiz
            own = lid == listing_id
            allowed, info = self._allowed(row) if own else (False, None)
            notify = self.notify and own and allowed
            before = self._notif_count() if notify else 0
            created = matching.match_listing(con, r, self.profiles, notify=notify)
            self.stats["matches"] += created
            sent = self._notif_count() - before if notify else 0
            self.stats["notifications"] += sent
            if own and first_seen and created and self.notify and info is not None:
                self.gate_events.append(dict(info, external_id=row["external_id"], matches=created,
                                             notified=sent > 0, allowed=allowed))

    def _notif_count(self):
        return self.con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]

    # ------------------------------------------------------------ yoxa çıxanlar
    def mark_removed(self, deal_type, property_type, district=None, price_period=None):
        """Sonuna qədər oxunmuş axında görünməyən köhnə elanları deaktiv edir. Sayı qaytarır.

        Axın = sorğunun filtri: Bina.az üçün rayon + tip + əməliyyat (district verilir),
        Tap.az üçün tip + əməliyyat + kirayə müddəti (price_period verilir; rayon filtri yoxdur).
        """
        cutoff = ts(self.started - REMOVE_AFTER)
        stamp = ts()
        where, args = ["source_id = ?", "active = 1", "deal_type = ?", "property_type = ?", "last_seen_at < ?"], \
            [self.source["id"], deal_type, property_type, cutoff]
        if district is not None:
            where.append("district = ?")
            args.append(district)
        if price_period is not None:
            where.append("price_period = ?")
            args.append(price_period)
        cur = self.con.execute(
            f"UPDATE listings SET active = 0, removed_at = ?, updated_at = ? WHERE {' AND '.join(where)} "
            "RETURNING dup_group",
            [stamp, stamp] + args,
        )
        groups = {r["dup_group"] for r in cur.fetchall() if r["dup_group"] is not None}
        n = cur.rowcount or 0
        self.stats["removed"] += n
        # təkrar qrupunda qalan nüsxələr: qrup yenidən hesablanır, uyğunluq yeni əsas nüsxəyə keçir
        for gid in groups:
            self.con.execute("UPDATE listings SET dup_group = NULL WHERE dup_group = ? AND active = 0", (gid,))
            rest = self.con.execute("SELECT * FROM listings WHERE dup_group = ? AND active = 1", (gid,)).fetchall()
            if rest:
                self._group_and_match(rest[0]["id"], rest[0]["price_period"] != "day")
        return n

    def commit(self):
        self.con.commit()

    # ------------------------------------------------------------ yekun
    def finish(self, ok, error=None, latency=0.0, label=""):
        """sync_runs, logs və sources-u yeniləyir, commit edir."""
        con, s = self.con, self.stats
        try:
            con.rollback()  # yarımçıq axının yazılmamış dəyişiklikləri (bloklanma/xəta anında)
        except Exception:
            pass
        duration = int(time.time() - self.t0)
        cur = con.execute(
            """INSERT INTO sync_runs(source_id, started_at, ok, latency, duration, checked, new_count, updated, removed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (self.source["id"], ts(self.started), 1 if ok else 0, round(latency, 2), duration,
             s["checked"], s["new"], s["updated"], s["removed"]),
        )
        details = (f"job=scraper-{self.source['key']} {label} run=#{cur.lastrowid} müddət={duration}s\n"
                   f"yeni={s['new']} yenilənən={s['updated']} silinən={s['removed']} "
                   f"şübhəli={s['suspicious']} atlanan={s['skipped']} uyğunluq={s['matches']} "
                   f"bildiriş={s['notifications']}{' (quiet)' if not self.notify else ''}")
        name = self.source["name"]
        if ok:
            add_log(con, "ok", name, f"Scraper tamamlandı — {s['checked']} elan yoxlandı, {s['new']} yeni tapıldı",
                    details)
        else:
            add_log(con, "err", name, f"Scraper dayandı: {error}", details)
        con.execute("UPDATE sources SET status = ?, last_sync = ? WHERE id = ?",
                    ("up" if ok else "down", ts(), self.source["id"]))
        con.commit()
