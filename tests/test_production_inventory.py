from datetime import date

from production_inventory import (
    available_stock_for_day,
    calculate_production_batches,
    sales_overstock_warnings,
)


def test_production_20_and_sales_15_remains_leftover_until_expiry():
    inventory = calculate_production_batches(
        [{
            "id": 1,
            "date": date(2026, 6, 1),
            "bread_type": "Pandesal",
            "products_produced": 20,
            "actual_qty_sold": 15,
            "price_per_product": 4.5,
        }],
        as_of=date(2026, 6, 3),
    )

    batch = inventory["batches"][0]
    assert batch["quantity_sold"] == 15
    assert batch["leftover_quantity"] == 5
    assert batch["waste_quantity"] == 0
    assert batch["expiry_date"] == date(2026, 6, 6)


def test_later_sale_consumes_old_batch_before_new_batch():
    inventory = calculate_production_batches(
        [
            {
                "id": 1,
                "date": "2026-06-01",
                "bread_type": "Pandesal",
                "products_produced": 20,
                "actual_qty_sold": 15,
            },
            {
                "id": 2,
                "date": "2026-06-03",
                "bread_type": "Pandesal",
                "products_produced": 10,
                "actual_qty_sold": 7,
            },
        ],
        as_of=date(2026, 6, 4),
    )

    by_day = {batch["production_date"].isoformat(): batch for batch in inventory["batches"]}
    assert by_day["2026-06-01"]["quantity_sold"] == 20
    assert by_day["2026-06-01"]["leftover_quantity"] == 0
    assert by_day["2026-06-03"]["quantity_sold"] == 2
    assert by_day["2026-06-03"]["leftover_quantity"] == 8
    assert [
        (allocation["sale_id"], allocation["batch_index"], allocation["quantity"])
        for allocation in inventory["allocations"]
    ] == [(1, 0, 15), (2, 0, 5), (2, 1, 2)]


def test_unsold_leftover_becomes_waste_on_expiry_date():
    inventory = calculate_production_batches(
        [{
            "id": 1,
            "date": "2026-06-01",
            "bread_type": "Pandesal",
            "products_produced": 20,
            "actual_qty_sold": 15,
        }],
        as_of=date(2026, 6, 6),
    )

    batch = inventory["batches"][0]
    assert batch["leftover_quantity"] == 0
    assert batch["waste_quantity"] == 5


def test_product_shelf_life_controls_expiry():
    inventory = calculate_production_batches(
        [{
            "id": 1,
            "date": "2026-06-01",
            "bread_type": "Pandesal",
            "products_produced": 20,
            "actual_qty_sold": 15,
        }],
        shelf_life_days={"Pandesal": 2},
        as_of=date(2026, 6, 3),
    )

    batch = inventory["batches"][0]
    assert batch["expiry_date"] == date(2026, 6, 3)
    assert batch["waste_quantity"] == 5


def test_available_stock_uses_older_batches_and_same_day_production():
    rows = [
        {
            "date": "2026-06-01",
            "bread_type": "Pandesal",
            "products_produced": 20,
            "actual_qty_sold": 15,
        },
        {
            "date": "2026-06-03",
            "bread_type": "Pandesal",
            "products_produced": 0,
            "actual_qty_sold": 5,
        },
    ]

    assert available_stock_for_day(rows, "Pandesal", "2026-06-03") == 5
    assert available_stock_for_day(rows, "Pandesal", "2026-06-03", produced=4) == 9


def test_overstock_requires_explicit_confirmation():
    rows = [{
        "date": "2026-06-01",
        "bread_type": "Pandesal",
        "products_produced": 20,
        "actual_qty_sold": 15,
    }]
    proposed = [{
        "date": "2026-06-02",
        "bread_type": "Pandesal",
        "products_produced": 0,
        "actual_qty_sold": 6,
    }]

    assert sales_overstock_warnings(rows, proposed) == [{
        "date": "2026-06-02",
        "bread_type": "Pandesal",
        "available": 5,
        "requested": 6,
    }]


def test_historical_sales_do_not_create_or_consume_production_stock():
    historical = {
        "date": "2026-03-01",
        "bread_type": "Ube Pandesal",
        "products_produced": 500,
        "actual_qty_sold": 214,
        "is_historical": 1,
    }

    inventory = calculate_production_batches([historical])

    assert inventory["batches"] == []
    assert inventory["allocations"] == []
    assert inventory["available_before_sale"] == {}
    assert sales_overstock_warnings([], [historical]) == []
