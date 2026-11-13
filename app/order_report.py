"""Read-only order cohort report. Amounts are order values, NOT paid revenue."""
import argparse
import csv
from datetime import date, datetime, timezone
import io
import json
import sqlite3
import sys

from .db import Database

STATUSES = ("PLACED", "FULFILLED", "CANCELLED")


def day_boundary(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("dates must use YYYY-MM-DD")
    return value + "T00:00:00.000Z"


def order_report(path, start, end):
    """Select by creation date [start,end); show current status at read time.

    Read orders once, not joined item rows: a multi-item order counts only once.
    Python integer accumulation keeps totals exact beyond SQLite SUM's int64 cap.
    """
    lower, upper = day_boundary(start), day_boundary(end)
    if lower >= upper:
        raise ValueError("end must be later than start")
    rows = {status: {"status": status, "order_count": 0, "order_value_cents": 0}
            for status in STATUSES}
    with Database(path, read_only=True).read_snapshot() as connection:
        for row in connection.execute(
            "SELECT status,total_cents FROM orders WHERE created_at>=? AND created_at<?",
            (lower, upper),
        ):
            if row["status"] not in rows or type(row["total_cents"]) is not int or row["total_cents"] < 0:
                raise ValueError("invalid order data")
            bucket = rows[row["status"]]
            bucket["order_count"] += 1
            bucket["order_value_cents"] += row["total_cents"]
    return {
        "start_inclusive_utc": lower, "end_exclusive_utc": upper,
        "reported_at_utc": datetime.now(timezone.utc).isoformat(),
        "basis": "created_at_cohort_current_status", "currency": "CNY",
        "amount_unit": "cents", "is_payment_revenue": False,
        "statuses": list(rows.values()),
        "non_cancelled_order_value_cents": sum(rows[s]["order_value_cents"] for s in ("PLACED", "FULFILLED")),
        "total_order_count": sum(r["order_count"] for r in rows.values()),
    }


def as_csv(report):
    stream = io.StringIO(newline="")
    fields = ["start_inclusive_utc", "end_exclusive_utc", "basis", "currency", "amount_unit",
              "status", "order_count", "order_value_cents"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in report["statuses"]:
        writer.writerow({**{key: report[key] for key in fields if key in report}, **row})
    return stream.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--start", required=True, help="UTC creation date inclusive, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC creation date exclusive, YYYY-MM-DD")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args(argv)
    try:
        report = order_report(args.database, args.start, args.end)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else as_csv(report), end="\n" if args.format == "json" else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
