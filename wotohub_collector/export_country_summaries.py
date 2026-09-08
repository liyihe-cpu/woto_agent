"""Export deduplicated handle summaries from one collector batch.

One CSV per top-N country by unique-handle count (named
<platform>_<country>_<label>.csv), plus a single combined
<platform>_else_<label>.csv for every other country.

Columns: 国家, 语言, handle, 粉丝量   (第四列=该查询的粉丝区间，非单个达人精确值)
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "collector.sqlite3"
HEADER = ("国家", "语言", "handle", "粉丝量")


def band(low: float, high: float) -> str:
    # Preserve the source interval so a split caused by the 10k cap stays clear.
    return f"{low:g}-{high:g}"


def write_csv(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="youtube", choices=("youtube", "ins", "instagram", "tiktok"))
    parser.add_argument("--top", type=int, default=4, help="leading countries split into their own file")
    parser.add_argument("--batch", help="only export this collector batch ID")
    parser.add_argument("--follower-min", type=int, default=5_000, help="inclusive follower floor")
    parser.add_argument("--follower-max", type=int, default=10_000, help="exclusive follower ceiling")
    parser.add_argument("--label", default="5k-1w", help="range label used in output file names")
    args = parser.parse_args()
    if args.follower_min < 0 or args.follower_max <= args.follower_min:
        parser.error("--follower-max must be greater than --follower-min")
    platform = "ins" if args.platform in ("ins", "instagram") else args.platform
    out_dir = ROOT / "output" / platform
    db = sqlite3.connect(DB_PATH)

    # Historical retries can hold the same handle more than once. Group so each
    # handle appears once per country/language, keeping the whole 5k-10k scope.
    sql = """
        SELECT t.country_code, t.language_code, h.handle, t.low, t.high
        FROM handles AS h
        JOIN tasks AS t ON t.id = h.task_id
        JOIN batches AS b ON b.id = t.batch_id
        WHERE b.platform = ?
          AND (? IS NULL OR b.id = ?)
          AND t.status IN ('done', 'incomplete')
          AND t.low >= ? AND t.high <= ?
        GROUP BY t.country_code, t.language_code, h.handle, t.low, t.high
        ORDER BY t.country_code, t.language_code, h.handle, t.low, t.high
    """
    # One row per country/language/handle; on re-runs keep the narrowest band.
    selected: dict[tuple[str, str, str], tuple[float, float]] = {}
    for country, language, handle, low, high in db.execute(
        sql, (platform, args.batch, args.batch, args.follower_min, args.follower_max - 1)
    ):
        key = (country, language, handle)
        old = selected.get(key)
        if old is None or (high - low, low, high) < (old[1] - old[0], old[0], old[1]):
            selected[key] = (low, high)
    db.close()

    # Rank countries by unique handles (across languages) to pick the top-N.
    count_by_country: dict[str, int] = defaultdict(int)
    seen = set()
    for (country, _lang, handle), (_lo, _hi) in selected.items():
        if (country, handle) not in seen:
            seen.add((country, handle))
            count_by_country[country] += 1
    top = sorted(count_by_country, key=lambda c: (-count_by_country[c], c))[: args.top]
    top_set = set(top)
    print(f"[{platform}] top-{args.top} by handles: " +
          ", ".join(f"{c}={count_by_country[c]}" for c in top))

    groups: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for (country, language, handle), (low, high) in selected.items():
        target = country if country in top_set else "else"
        groups[target].append((country, language, handle, band(low, high)))

    for target in (*top, "else"):
        rows = sorted(groups[target], key=lambda r: (r[0], r[1], r[2], r[3]))
        path = out_dir / f"{platform}_{target}_{args.label}.csv"
        write_csv(path, rows)
        print(f"{path.name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
