#!/usr/bin/env python3
"""backfill_gap_down_log.py — Identify and optionally patch gap_down data gaps.

Historical daily_scan_log rows written before the gap-down screener pass was
added have no gap_down entries.  Because Finviz does not expose historical
intraday screener results, we cannot reconstruct those tickers automatically.
This script does two things:

  1.  REPORT mode (default) — prints every date in daily_scan_log that has
      gap/trend/squeeze rows but zero gap_down rows, i.e. dates where the
      gap_down pass simply was not running yet.

  2.  PATCH mode (--date / --tickers) — manually inject gap_down tickers for
      a specific past date if you have a record of them (e.g. from a broker
      activity log, a personal trading journal, or external Finviz screenshots
      saved at the time).  Existing gap_down rows for that date+slot are
      replaced, other screener_pass rows are left untouched.

Usage
-----
  # Show all dates missing gap_down data
  python backfill_gap_down_log.py

  # Inject gap_down tickers for a specific date (morning slot)
  python backfill_gap_down_log.py --date 2025-03-10 --tickers AAPL,TSLA,NVDA

  # Inject gap_down tickers for the midday slot
  python backfill_gap_down_log.py --date 2025-03-10 --slot midday --tickers AAPL,TSLA

Environment
-----------
  SUPABASE_URL and SUPABASE_KEY (or SERVICE_KEY) must be set, exactly as the
  main application expects them.
"""

import argparse
import datetime
import os
import sys

try:
    from supabase import create_client
except ImportError:
    print("ERROR: supabase-py not installed.  Run: pip install supabase", file=sys.stderr)
    sys.exit(1)


def _get_client():
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
    )
    if not url or not key:
        print(
            "ERROR: SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY) must be set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return create_client(url, key)


def report_missing_gap_down(supabase) -> list[str]:
    """Print all dates that have scan rows but no gap_down rows.

    Returns a list of ISO-format date strings for dates that are missing
    gap_down data.
    """
    print("Querying daily_scan_log for dates missing gap_down entries …")

    def _fetch_all_pages(query_builder) -> list:
        """Fetch all rows from a Supabase query using range-based pagination.

        Supabase/PostgREST returns at most 1 000 rows per request by default.
        We paginate in 1 000-row chunks until an empty page is returned.
        """
        PAGE = 1000
        offset = 0
        accumulated: list = []
        while True:
            res = query_builder.range(offset, offset + PAGE - 1).execute()
            if not res.data:
                break
            accumulated.extend(res.data)
            if len(res.data) < PAGE:
                break
            offset += PAGE
        return accumulated

    all_rows = _fetch_all_pages(
        supabase.table("daily_scan_log")
        .select("scan_date")
        .order("scan_date", desc=False)
    )
    if not all_rows:
        print("No rows found in daily_scan_log — nothing to report.")
        return []

    all_dates: set[str] = {row["scan_date"][:10] for row in all_rows}

    gd_rows = _fetch_all_pages(
        supabase.table("daily_scan_log")
        .select("scan_date")
        .eq("screener_pass", "gap_down")
        .order("scan_date", desc=False)
    )
    gd_dates: set[str] = {row["scan_date"][:10] for row in gd_rows} if gd_rows else set()

    missing: list[str] = sorted(all_dates - gd_dates)

    if not missing:
        print("No missing gap_down dates found — all scan dates have gap_down data.")
        return []

    if gd_dates:
        first_gd = min(gd_dates)
        print(
            f"\nGap-down data is present from {first_gd} onward.\n"
            f"The following {len(missing)} date(s) predate that and have NO gap_down rows.\n"
            "Because Finviz does not provide historical screener results, these dates\n"
            "cannot be backfilled automatically.  Use --date / --tickers if you have\n"
            "an external record of which tickers were gapping down on those days.\n"
        )
    else:
        print(
            f"\nNo gap_down data exists anywhere in daily_scan_log yet.\n"
            f"{len(missing)} date(s) are affected:\n"
        )

    for d in missing:
        print(f"  {d}")

    return missing


def patch_gap_down(
    supabase,
    scan_date: str,
    tickers: list[str],
    slot: str = "morning",
) -> None:
    """Inject gap_down rows for a specific date/slot.

    Existing gap_down rows for the same (scan_date, slot) are deleted first
    to avoid duplicates.  Other screener_pass rows (gap/trend/squeeze) are
    not touched.

    Args:
        supabase:  Supabase client.
        scan_date: ISO-format date string, e.g. '2025-03-10'.
        tickers:   List of ticker symbols to mark as gap_down.
        slot:      'morning' or 'midday'.
    """
    seen_tickers: set[str] = set()
    tickers_clean: list[str] = []
    for t in tickers:
        t = t.strip().upper()
        if t and t not in seen_tickers:
            tickers_clean.append(t)
            seen_tickers.add(t)
    if not tickers_clean:
        print("No tickers supplied — nothing to insert.")
        return

    print(
        f"Patching {scan_date} / {slot} with {len(tickers_clean)} gap_down ticker(s): "
        f"{', '.join(tickers_clean)}"
    )

    supabase.table("daily_scan_log").delete().eq("scan_date", scan_date).eq(
        "slot", slot
    ).eq("screener_pass", "gap_down").execute()

    rows = [
        {"scan_date": scan_date, "ticker": t, "screener_pass": "gap_down", "slot": slot}
        for t in tickers_clean
    ]
    supabase.table("daily_scan_log").insert(rows).execute()
    print(f"Done — inserted {len(rows)} gap_down row(s) for {scan_date}/{slot}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report or patch gap_down entries in daily_scan_log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Date to patch (patch mode).  If omitted, report mode runs.",
    )
    parser.add_argument(
        "--tickers",
        metavar="AAPL,TSLA,…",
        help="Comma-separated list of gap_down tickers to inject for --date.",
    )
    parser.add_argument(
        "--slot",
        choices=["morning", "midday"],
        default="morning",
        help="Scan slot to target (default: morning).",
    )
    args = parser.parse_args()

    supabase = _get_client()

    if args.date:
        try:
            datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: --date must be in YYYY-MM-DD format, got: {args.date}", file=sys.stderr)
            sys.exit(1)

        tickers = [t.strip() for t in (args.tickers or "").split(",") if t.strip()]
        if not tickers:
            print("ERROR: --tickers is required when --date is supplied.", file=sys.stderr)
            sys.exit(1)

        patch_gap_down(supabase, args.date, tickers, slot=args.slot)
    else:
        report_missing_gap_down(supabase)


if __name__ == "__main__":
    main()
