"""Preview or populate derived FIFO batches from the existing sales_data table."""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

import mysql.connector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from production_inventory import calculate_production_batches


MANILA_TZ = timezone(timedelta(hours=8))


def connect():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST") or os.environ.get("R_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT") or os.environ.get("R_PORT", 3306)),
        user=os.environ.get("DB_USER") or os.environ.get("R_USER", "root"),
        password=os.environ.get("DB_PASSWORD") or os.environ.get("R_PASS", ""),
        database=os.environ.get("DB_NAME") or os.environ.get("R_DATABASE", "prediction_system"),
        connect_timeout=15,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Backfill FIFO production batches without changing sales_data."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replace only the derived batch/allocation tables after showing the preview.",
    )
    args = parser.parse_args()

    conn = connect()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT id, date, bread_type, products_produced,
                      actual_qty_sold, is_historical, price_per_product
               FROM sales_data ORDER BY bread_type, date, id"""
        )
        sales_rows = cursor.fetchall()
        cursor.execute(
            """SELECT id, bread_type, shelf_life_days, default_price
               FROM bread_type_catalog"""
        )
        products = {
            row["bread_type"]: row for row in cursor.fetchall()
        }
        shelf_life = {
            name: max(1, int(row["shelf_life_days"] or 5))
            for name, row in products.items()
        }
        inventory = calculate_production_batches(
            sales_rows, shelf_life, as_of=datetime.now(MANILA_TZ).date()
        )
        batches = inventory["batches"]
        allocations = inventory["allocations"]
        overstock_rows = []
        for row in sales_rows:
            if row.get("is_historical") in (1, True, "1"):
                continue
            day = row["date"].isoformat()
            key = (row["bread_type"], day)
            available = inventory["available_before_sale"].get(key, 0)
            sold = int(row["actual_qty_sold"] or 0)
            if sold > available:
                overstock_rows.append((day, row["bread_type"], sold, available))

        total_leftover = sum(batch["leftover_quantity"] for batch in batches)
        total_waste = sum(batch["waste_quantity"] for batch in batches)
        print("BACKFILL PREVIEW (read-only)" if not args.apply else "BACKFILL PREVIEW")
        print(f"Sales/production rows read: {len(sales_rows)}")
        print(f"Production batches to create: {len(batches)}")
        print(f"FIFO allocations to create: {len(allocations)}")
        print(f"Unexpired leftover as of Manila date: {total_leftover}")
        print(f"Expired waste as of Manila date: {total_waste}")
        print(f"Rows exceeding available stock (legacy data retained): {len(overstock_rows)}")
        for day, bread, sold, available in overstock_rows[:20]:
            print(f"  {day} | {bread}: {sold} sold, {available} allocated stock")
        if len(overstock_rows) > 20:
            print(f"  ... and {len(overstock_rows) - 20} more")

        if not args.apply:
            print("No data was changed. Review this preview before using --apply.")
            return 0

        for table in ("sales_batch_allocations", "production_batches"):
            cursor.execute(f"DELETE FROM `{table}`")
        batch_ids = {}
        for batch in batches:
            product = products.get(batch["bread_type"], {})
            unit_price = batch["unit_price"] or float(product.get("default_price") or 0)
            cursor.execute(
                """INSERT INTO production_batches (
                       product_id, bread_type, production_date, quantity_produced,
                       quantity_sold, leftover_quantity, waste_quantity, expiry_date,
                       unit_price, source_sales_id
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    product.get("id"),
                    batch["bread_type"],
                    batch["production_date"],
                    batch["quantity_produced"],
                    batch["quantity_sold"],
                    batch["leftover_quantity"],
                    batch["waste_quantity"],
                    batch["expiry_date"],
                    unit_price,
                    batch["source_sales_id"],
                ),
            )
            batch_ids[batch["source_index"]] = cursor.lastrowid

        for allocation in allocations:
            batch_id = batch_ids.get(allocation["batch_index"])
            if allocation["sale_id"] and batch_id:
                cursor.execute(
                    """INSERT INTO sales_batch_allocations (
                           sale_id, batch_id, allocated_quantity
                       ) VALUES (%s, %s, %s)""",
                    (allocation["sale_id"], batch_id, allocation["quantity"]),
                )
        conn.commit()
        print("Backfill completed. sales_data and raw_sales_uploads were not modified.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
