#!/usr/bin/env bash
# python -m scraper.auto <əmr> işlədir və çıxış kodunu Actions üçün çevirir.
#   0 = uğurlu (cooldown / kilid səbəbilə çıxış da 0-dır)
#   3 = bloklanma (cooldown başladı): BLOCKED_OK=1 olanda iş UĞURLU bitir (xəbərdarlıqla), əks halda uğursuz
#   2 = baseline yoxdur, 1 və digərləri = gözlənilməyən xəta → iş uğursuz
# set -x YOXDUR: əmrlər və mühit dəyişənləri loqa çap olunmur.
set -u
"${PYTHON:-python}" -m scraper.auto "$@"
code=$?
case "$code" in
  0) exit 0 ;;
  3)
    if [ "${BLOCKED_OK:-0}" = "1" ]; then
      echo "::warning title=Bloklanma::sayt sorğunu rədd etdi — cooldown başladı, fasilədən sonra avtomatik davam edəcək"
      exit 0
    fi
    echo "::error title=Bloklanma::sayt sorğunu rədd etdi (scraper.auto $1)"
    exit 3 ;;
  2) echo "::error title=Baseline yoxdur::əvvəl baseline workflow-unu əl ilə işə salın"; exit 2 ;;
  *) echo "::error title=Xəta::scraper.auto $1 kodu $code ilə bitdi"; exit "$code" ;;
esac
