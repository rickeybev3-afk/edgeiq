"""One-time migration: normalise predicted/actual labels in accuracy_tracker.

Run once from the project root:
    python migrate_accuracy_labels.py

The script fetches every row from the accuracy_tracker table in pages,
applies the same _clean_structure_label() normalisation used at write-time
in backend.py, and updates any row whose label would change.  Rows that are
already clean are skipped (no unnecessary writes).
"""

import sys

PAGE_SIZE = 1000


def _clean_structure_label(raw: str) -> str:
    import re
    s = re.sub(r"[^\w\s()/\-]", "", str(raw)).strip()
    return s[:30] if len(s) > 30 else s


def _fetch_all_rows(supabase) -> list:
    """Page through the full accuracy_tracker table and return all rows."""
    rows: list = []
    offset = 0
    while True:
        batch = (
            supabase.table("accuracy_tracker")
            .select("id,predicted,actual")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data or []
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    try:
        from backend import _clean_structure_label as _backend_clean  # noqa: F401
        _clean = _backend_clean
    except (ImportError, AttributeError):
        _clean = _clean_structure_label

    try:
        from backend import supabase
    except ImportError:
        print("ERROR: could not import supabase from backend.py", file=sys.stderr)
        sys.exit(1)

    if not supabase:
        print("ERROR: supabase client is not initialised (check credentials).",
              file=sys.stderr)
        sys.exit(1)

    print("Fetching all rows from accuracy_tracker …")
    try:
        rows = _fetch_all_rows(supabase)
    except Exception as exc:
        print(f"ERROR reading accuracy_tracker: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(rows)} rows fetched.")

    updated = skipped = errors = 0
    for row in rows:
        row_id    = row.get("id")
        predicted = row.get("predicted") or ""
        actual    = row.get("actual") or ""

        clean_predicted = _clean(predicted)
        clean_actual    = _clean(actual)

        if clean_predicted == predicted and clean_actual == actual:
            skipped += 1
            continue

        patch = {}
        if clean_predicted != predicted:
            patch["predicted"] = clean_predicted
        if clean_actual != actual:
            patch["actual"] = clean_actual

        try:
            supabase.table("accuracy_tracker").update(patch).eq("id", row_id).execute()
            updated += 1
            if updated % 50 == 0:
                print(f"  … {updated} rows updated so far")
        except Exception as exc:
            print(f"  WARN: could not update row {row_id}: {exc}")
            errors += 1

    print(
        f"\nDone — {updated} rows updated, {skipped} already clean, {errors} errors."
    )


if __name__ == "__main__":
    main()
