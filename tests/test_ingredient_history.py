from datetime import datetime, timedelta
from decimal import Decimal

import app as app_module


def make_logged_in_client():
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
    return client


def test_manual_stock_adjustments_record_history_and_reject_over_deduction(monkeypatch):
    ingredients = []
    history = []
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "INGREDIENTS", ingredients)
    monkeypatch.setattr(app_module, "INGREDIENT_HISTORY", history)
    client = make_logged_in_client()

    created = client.post(
        "/ingredients/add",
        json={"name": "Flour", "current_stock": 20, "unit": "kg"},
    )
    ingredient_id = created.get_json()["ingredient"]["id"]
    assert [(row["action_type"], row["quantity_changed"], row["stock_before"], row["stock_after"])
            for row in history] == [("INITIAL", 20.0, 0.0, 20.0)]

    deducted = client.post(
        f"/api/ingredients/{ingredient_id}/adjust",
        json={"type": "out", "amount": 5, "note": "used in production"},
    )
    assert deducted.status_code == 200
    assert ingredients[0]["current_stock"] == 15
    assert history[-1]["action_type"] == "DEDUCT"
    assert (history[-1]["quantity_changed"], history[-1]["stock_before"], history[-1]["stock_after"]) == (
        -5.0, 20.0, 15.0
    )
    assert history[-1]["remarks"] == "used in production"

    rejected = client.post(
        f"/api/ingredients/{ingredient_id}/adjust",
        json={"type": "out", "amount": 16},
    )
    assert rejected.status_code == 400
    assert "only 15.0000 kg is in stock" in rejected.get_json()["error"]
    assert ingredients[0]["current_stock"] == 15
    assert len(history) == 2


def test_manual_mysql_adjustment_writes_stock_and_history_before_commit(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.queries = []
            self.fetch_count = 0

        def execute(self, query, params=None):
            self.queries.append((query, params))

        def fetchone(self):
            self.fetch_count += 1
            if self.fetch_count == 1:
                return {
                    "id": 7,
                    "name": "Flour",
                    "current_stock": Decimal("10"),
                    "unit": "kg",
                    "reorder_threshold": Decimal("2"),
                    "last_restock": None,
                }
            return {
                "id": 7,
                "name": "Flour",
                "current_stock": Decimal("12"),
                "unit": "kg",
                "reorder_threshold": Decimal("2"),
                "last_restock": None,
            }

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.committed_queries = None

        def cursor(self, **kwargs):
            return self.cursor_instance

        def commit(self):
            self.committed_queries = list(self.cursor_instance.queries)

        def rollback(self):
            raise AssertionError("Unexpected rollback")

        def close(self):
            pass

    connection = FakeConnection()
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(app_module, "get_db_connection", lambda: connection)

    response = make_logged_in_client().post(
        "/api/ingredients/7/adjust",
        json={"type": "in", "amount": 2, "note": "delivery"},
    )

    assert response.status_code == 200
    assert connection.committed_queries is not None
    committed_sql = [query for query, _ in connection.committed_queries]
    assert any(query.startswith("UPDATE ingredients") for query in committed_sql)
    assert any(query.startswith("INSERT INTO ingredient_transactions") for query in committed_sql)
    history_position = next(i for i, query in enumerate(committed_sql) if "INSERT INTO ingredient_history" in query)
    update_position = next(i for i, query in enumerate(committed_sql) if query.startswith("UPDATE ingredients"))
    assert history_position > update_position


def test_history_date_bounds_use_manila_calendar_and_custom_inclusive_end():
    week_start, week_end = app_module.ingredient_history_date_bounds("week")
    assert week_start.weekday() == 0
    assert week_end - week_start == timedelta(days=7)

    month_start, month_end = app_module.ingredient_history_date_bounds("month")
    assert month_start.day == 1
    assert month_end.day == 1
    assert month_end.month != month_start.month or month_end.year != month_start.year

    start, end = app_module.ingredient_history_date_bounds("custom", "2026-10-02", "2026-10-09")
    assert start == datetime(2026, 10, 2)
    assert end == datetime(2026, 10, 10)


def test_history_filters_and_summaries_combine_by_ingredient_and_date(monkeypatch):
    flour = {
        "id": 1,
        "name": "Flour",
        "ingredient_name": "Flour",
        "unit": "kg",
        "created_at": datetime.now(app_module.MANILA_TZ),
    }
    sugar = {
        "id": 2,
        "name": "Sugar",
        "ingredient_name": "Sugar",
        "unit": "kg",
        "created_at": datetime.now(app_module.MANILA_TZ),
    }
    rows = [
        {**flour, "id": 1, "ingredient_id": 1, "action_type": "INITIAL", "quantity_changed": 20, "stock_before": 0,
         "stock_after": 20, "remarks": "Initial"},
        {**flour, "id": 2, "ingredient_id": 1, "action_type": "DEDUCT", "quantity_changed": -5, "stock_before": 20,
         "stock_after": 15, "remarks": "Production"},
        {**sugar, "id": 3, "ingredient_id": 2, "action_type": "ADD", "quantity_changed": 8, "stock_before": 1,
         "stock_after": 9, "remarks": "Delivery"},
    ]
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "INGREDIENT_HISTORY", rows)

    result = app_module.fetch_ingredient_history(1, "today", "", "", 1)

    assert [row["ingredient_name"] for row in result["rows"]] == ["Flour", "Flour"]
    assert result["total_rows"] == 2


def test_ingredient_page_shows_history_filters_and_records(monkeypatch):
    ingredient = {
        "id": 5,
        "name": "Flour",
        "current_stock": 15.0,
        "unit": "kg",
        "reorder_threshold": 1.0,
        "last_restock": None,
    }
    record = {
        "id": 1,
        "ingredient_id": 5,
        "ingredient_name": "Flour",
        "action_type": "ADD",
        "quantity_changed": 10.0,
        "unit": "kg",
        "stock_before": 5.0,
        "stock_after": 15.0,
        "remarks": "Delivery",
        "created_at": datetime.now(app_module.MANILA_TZ),
    }
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "fetch_ingredients_from_db", lambda: [ingredient])
    monkeypatch.setattr(app_module, "INGREDIENT_HISTORY", [record])
    monkeypatch.setattr(app_module, "fetch_monthly_expenses", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "fetch_monthly_expense_total", lambda *args, **kwargs: 0.0)

    response = make_logged_in_client().get("/ingredients?ingredient=5&range=today")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="history-ingredient-picker"' in html
    assert 'name="range"' in html
    assert "This Week" in html
    assert "Custom range" in html
    assert "Delivery" in html
    assert "Stock before" in html
    assert "Stock after" in html
    assert "Stock change totals" not in html
    assert "Net change" not in html
