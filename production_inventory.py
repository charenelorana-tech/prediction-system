"""Pure FIFO calculations for dated production batches."""

from datetime import date, datetime, timedelta


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def calculate_production_batches(sales_rows, shelf_life_days=None, as_of=None):
    """Return FIFO allocations and current leftover/waste for each production day."""
    shelf_life_days = shelf_life_days or {}
    as_of = _day(as_of or date.today())
    grouped = {}
    for index, row in enumerate(sales_rows or []):
        if row.get("is_historical") in (1, True, "1"):
            continue
        bread = str(row.get("bread_type") or "")
        if not bread:
            continue
        day = _day(row.get("date"))
        grouped.setdefault(bread, []).append((day, index, row))

    batches = []
    allocations = []
    available_before_sale = {}

    for bread, entries in grouped.items():
        entries.sort(key=lambda item: (item[0], item[1]))
        shelf_days = max(1, int(shelf_life_days.get(bread, 5) or 5))
        product_batches = []

        for production_day, row_index, row in entries:
            produced = max(0, int(row.get("products_produced") or 0))
            batch = None
            if produced:
                expiry = production_day + timedelta(days=shelf_days)
                batch = {
                    "source_index": row_index,
                    "source_sales_id": row.get("id"),
                    "bread_type": bread,
                    "production_date": production_day,
                    "expiry_date": expiry,
                    "quantity_produced": produced,
                    "quantity_sold": 0,
                    "remaining": produced,
                    "waste_quantity": 0,
                    "leftover_quantity": 0,
                    "unit_price": float(row.get("price_per_product") or 0),
                }
                product_batches.append(batch)

            active_batches = [
                item for item in product_batches
                if item["expiry_date"] > production_day and item["remaining"] > 0
            ]
            key = (bread, production_day.isoformat())
            available_before_sale[key] = sum(item["remaining"] for item in active_batches)
            sold_remaining = max(0, int(row.get("actual_qty_sold") or 0))
            sale_id = row.get("id")
            for eligible in active_batches:
                if sold_remaining <= 0:
                    break
                allocated = min(eligible["remaining"], sold_remaining)
                if allocated:
                    eligible["remaining"] -= allocated
                    eligible["quantity_sold"] += allocated
                    sold_remaining -= allocated
                    allocations.append({
                        "sale_id": sale_id,
                        "sale_index": row_index,
                        "batch_index": eligible["source_index"],
                        "quantity": allocated,
                    })

        for item in product_batches:
            if item["expiry_date"] <= as_of:
                item["waste_quantity"] = item["remaining"]
            else:
                item["leftover_quantity"] = item["remaining"]
            batches.append(item)

    return {
        "batches": batches,
        "allocations": allocations,
        "available_before_sale": available_before_sale,
    }


def sales_overstock_warnings(existing_rows, proposed_rows, shelf_life_days=None):
    """Return new/edited sales that exceed eligible stock before that day's sale."""
    proposed_rows = [
        row for row in proposed_rows
        if row.get("is_historical") not in (1, True, "1")
    ]
    if not proposed_rows:
        return []
    proposed_by_key = {
        (str(row.get("bread_type") or ""), _day(row.get("date")).isoformat()): row
        for row in proposed_rows
    }
    merged = [
        row for row in existing_rows
        if (str(row.get("bread_type") or ""), _day(row.get("date")).isoformat())
        not in proposed_by_key
    ]
    merged.extend(proposed_rows)
    inventory = calculate_production_batches(merged, shelf_life_days)
    warnings = []

    for row in proposed_rows:
        key = (str(row.get("bread_type") or ""), _day(row.get("date")).isoformat())
        available = inventory["available_before_sale"].get(key, 0)
        sold = max(0, int(row.get("actual_qty_sold") or 0))
        if sold > available:
            warnings.append({
                "date": key[1],
                "bread_type": key[0],
                "available": available,
                "requested": sold,
            })
    return warnings


def available_stock_for_day(sales_rows, bread_type, target_date, produced=None,
                            shelf_life_days=None):
    """Compute stock available before the selected day's sales are entered."""
    target_day = _day(target_date)
    target_record = next((
        row for row in sales_rows or []
        if row.get("bread_type") == bread_type and _day(row.get("date")) == target_day
    ), {})
    production = (
        max(0, int(produced)) if produced is not None
        else max(0, int(target_record.get("products_produced") or 0))
    )
    rows = [
        row for row in sales_rows or []
        if row.get("bread_type") != bread_type or _day(row.get("date")) != target_day
    ]
    rows.append({
        "date": target_day,
        "bread_type": bread_type,
        "products_produced": production,
        "actual_qty_sold": 0,
    })
    result = calculate_production_batches(
        rows, shelf_life_days, as_of=target_day
    )
    return result["available_before_sale"].get((bread_type, target_day.isoformat()), 0)
