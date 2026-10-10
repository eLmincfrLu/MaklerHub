"""Scraper nəticələrini Neon (PostgreSQL) bazasına yazır.

Qaydalar (Gün 10 planı):
- Yeni elan → listings + price_history, sonra uyğunlaşdırma (günlük kirayə istisna).
- Görülən elan → last_seen_at yenilənir; qiymət dəyişibsə price_history-yə yeni sətir.
- Yoxa çıxan elan → yalnız axın sonuna qədər oxunubsa (hasNextPage=false) VƏ elan
  sayğı başlanğıcından ən azı 24 saat əvvəl görülübsə: active=0, removed_at=indi.
- Hər axından sonra commit olunur, ona görə bloklanma olsa da toplanan məlumat itmir.
"""
import json
import time
from collections import Counter
from datetime import timedelta

from maklerhub import matching
from maklerhub.db import add_log
from maklerhub.helpers import now, ts

REMOVE_AFTER = timedelta(hours=24)


class Store:
    def __init__(self, con, source_key="bina", notify=True):
        self.con = con
        self.notify = notify  # False (--quiet): matching işləyir, bildiriş yaranmır
        self.source = con.execute("SELECT * FROM sources WHERE key = ?", (source_key,)).fetchone()
        if not self.source:
            raise RuntimeError(f"sources cədvəlində '{source_key}' yoxdur")
        self.profiles = con.execute("SELECT * FROM profiles WHERE status = 'active'").fetchall()
        self.started = now()
        self.t0 = time.time()
        self.stats = Counter()

    # ------------------------------------------------------------ bir elan
    def save(self, row):
        """Bir normalizə olunmuş elanı yazır. Nəticə: new | updated | seen | skipped."""
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

        old = con.execute("SELECT id, price, active, suspicious FROM listings WHERE source_id = ? AND external_id = ?",
                          (sid, row["external_id"])).fetchone()
        if old is None:
            cur = con.execute(
                """INSERT INTO listings(source_id, external_id, url, title, suffix, district, rooms, area, floor,
                       floors, price, price_period, deal_type, property_type, building, renovation, metro_name,
                       images, suspicious, active, posted_at, created_at, updated_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
                (sid, row["external_id"], row["url"], row["title"], "kirayə" if row["deal_type"] == "rent" else "",
                 row["district"], row["rooms"], row["area"], row["floor"], row["floors"], price,
                 row["price_period"], row["deal_type"], row["property_type"], row["property_type"], "",
                 row["metro_name"], images, row["suspicious"], stamp, stamp, stamp, stamp),
            )
            lid = cur.lastrowid
            con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?, ?, ?)",
                        (lid, price, stamp))
            self.stats["new"] += 1
            if matchable:
                self._match(lid)
            return "new"

        lid = old["id"]
        price_changed = old["price"] != price
        reactivated = not old["active"]
        suspicious_changed = old["suspicious"] != row["suspicious"]
        con.execute("UPDATE listings SET last_seen_at = ?, images = ?, suspicious = ? WHERE id = ?",
                    (stamp, images, row["suspicious"], lid))
        if price_changed:
            con.execute("UPDATE listings SET price = ?, updated_at = ? WHERE id = ?", (price, stamp, lid))
            con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?, ?, ?)",
                        (lid, price, stamp))
        if reactivated:
            con.execute("UPDATE listings SET active = 1, removed_at = NULL, updated_at = ? WHERE id = ?",
                        (stamp, lid))
            self.stats["reactivated"] += 1
        if not (price_changed or reactivated or suspicious_changed):
            return "seen"
        self.stats["updated"] += 1
        if matchable:
            self._match(lid)
        return "updated"

    def _match(self, listing_id):
        if not self.profiles:
            return
        row = self.con.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        before = self._notif_count() if self.notify else 0
        self.stats["matches"] += matching.match_listing(self.con, row, self.profiles, notify=self.notify)
        if self.notify:
            self.stats["notifications"] += self._notif_count() - before

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
            f"UPDATE listings SET active = 0, removed_at = ?, updated_at = ? WHERE {' AND '.join(where)}",
            [stamp, stamp] + args,
        )
        n = cur.rowcount or 0
        self.stats["removed"] += n
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
