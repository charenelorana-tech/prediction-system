from io import BytesIO
from werkzeug.datastructures import FileStorage

import app as app_module
from app import parse_csv_upload


def test_cutoff_sales_save_rejects_sold_greater_than_actual_without_mutation(monkeypatch):
    sales_data = [{
        "date": "2026-10-11",
        "bread_type": "Pandesal",
        "products_produced": 8,
        "actual_qty_sold": 2,
        "price_per_product": 4.5,
    }]
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "SALES_DATA", sales_data)

    saved, error = app_module.save_cutoff_sales_records([{
        "date": "2026-10-11",
        "bread_type": "Pandesal",
        "actual_qty_sold": 9,
        "price_per_product": 4.5,
    }])

    assert not saved
    assert "cannot exceed actual production (8)" in error
    assert sales_data[0]["actual_qty_sold"] == 2


def test_cutoff_sales_save_requires_positive_actual_production(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "SALES_DATA", [])

    saved, error = app_module.save_cutoff_sales_records([{
        "date": "2026-10-11",
        "bread_type": "Pandesal",
        "actual_qty_sold": 0,
        "price_per_product": 4.5,
    }])

    assert not saved
    assert "Enter actual production first" in error
    assert app_module.SALES_DATA == []


def test_cutoff_sales_save_rejects_non_integer_sold_quantities(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "SALES_DATA", [])

    for invalid_quantity in (1.5, "1.5", True, "-1"):
        saved, error = app_module.save_cutoff_sales_records([{
            "date": "2026-10-11",
            "bread_type": "Pandesal",
            "actual_qty_sold": invalid_quantity,
            "price_per_product": 4.5,
        }])

        assert not saved
        assert "invalid sold quantity" in error
        assert app_module.SALES_DATA == []


def test_cutoff_sales_save_rejects_pre_cutoff_dates(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "SALES_DATA", [])

    saved, error = app_module.save_cutoff_sales_records([{
        "date": "2026-10-10",
        "bread_type": "Pandesal",
        "actual_qty_sold": 1,
        "price_per_product": 4.5,
    }])

    assert not saved
    assert "read-only" in error
    assert app_module.SALES_DATA == []


def test_cutoff_sales_save_updates_only_sold_and_price_in_mysql(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append((query, params))

        def fetchone(self):
            return {"products_produced": 10}

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.fake_cursor = FakeCursor()
            self.committed = False

        def cursor(self, **kwargs):
            return self.fake_cursor

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("Unexpected rollback")

        def close(self):
            pass

    connection = FakeConnection()
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(app_module, "get_db_connection", lambda: connection)

    saved, error = app_module.save_cutoff_sales_records([{
        "date": "2026-10-11",
        "bread_type": "Pandesal",
        "actual_qty_sold": 4,
        "price_per_product": 4.5,
    }])

    assert saved
    assert error is None
    insert = connection.fake_cursor.queries[-1][0]
    assert "actual_qty_sold = VALUES(actual_qty_sold)" in insert
    assert "products_produced = VALUES(products_produced)" not in insert
    assert not any("DELETE" in query.upper() for query, _ in connection.fake_cursor.queries)
    assert connection.committed


def test_production_actual_quantities_api_returns_values_for_each_date(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "SALES_DATA", [
        {
            "date": "2026-10-11",
            "bread_type": "Pandesal",
            "products_produced": 12,
        },
        {
            "date": "2026-10-12",
            "bread_type": "Pandesal",
            "products_produced": 0,
        },
    ])
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.get(
        "/api/production/actual-quantities?start_date=2026-10-11&scope_days=2&bread_type=Pandesal"
    )

    assert response.status_code == 200
    assert response.get_json()["production"] == [
        {"date": "2026-10-11", "bread_type": "Pandesal", "quantity": 12},
        {"date": "2026-10-12", "bread_type": "Pandesal", "quantity": 0},
    ]


def test_sales_log_form_has_read_only_produced_and_sold_input(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "SALES_DATA", [])
    monkeypatch.setattr(app_module, "fetch_monthly_expense_total", lambda *args, **kwargs: 0)
    monkeypatch.setattr(app_module, "fetch_all_expenses_total", lambda *args, **kwargs: 0)
    monkeypatch.setattr(app_module, "fetch_monthly_expenses", lambda *args, **kwargs: {})
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.get("/sales-log")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-production-endpoint="/api/production/actual-quantities"' in html
    assert 'class="input produced-value" type="text" value="Loading…" disabled' in html
    assert 'class="input sold-quantity"' in html
    assert 'name="produced"' not in html
    assert "Enter actual production first" in html


def test_parse_csv_upload_accepts_spaced_and_case_insensitive_headers():
    upload = FileStorage(
        stream=BytesIO(
            b"DATE,Bread Type,Products Produced,Actual Qty Sold,Price Per Product\n"
            b"10/09/2026,Cheese Bread,120,100,5.50\n"
        ),
        filename="sales.csv",
    )

    records = parse_csv_upload(upload)

    assert records == [
        {
            "date": "2026-10-09",
            "bread_type": "Cheese Bread",
            "products_produced": 120,
            "actual_qty_sold": 100,
            "waste_returns": 0,
            "price_per_product": 5.5,
            "temperature": 30.0,
            "is_holiday": 0,
            "is_promotion": 0,
            "sacks_used": 0,
            "plates_used": 0,
            "expense_amount": 0.0,
            "is_historical": 0,
            "id": None,
        }
    ]


def test_add_sales_product_persists_and_updates_runtime_catalog(monkeypatch):
    monkeypatch.setattr(app_module, "BREAD_TYPES", list(app_module.BREAD_TYPES))
    monkeypatch.setattr(app_module, "BREAD_PRICES", dict(app_module.BREAD_PRICES))
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "INACTIVE_BREAD_TYPES", set())
    persisted_products = []
    monkeypatch.setattr(
        app_module,
        "persist_custom_bread_type",
        lambda name, price: (persisted_products.append((name, price)) or (True, None)),
    )
    monkeypatch.setattr(app_module, "retrain_model_async", lambda sales: None)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.post(
        "/sales-log/add-product",
        data={"product_name": "  Cardamom   Roll  ", "product_price": "7.25"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "product": "Cardamom Roll",
        "price": 7.25,
        "product_id": None,
        "reactivated": False,
        "delete_url": "",
    }
    assert "Cardamom Roll" in app_module.BREAD_TYPES
    assert app_module.BREAD_PRICES["Cardamom Roll"] == 7.25
    assert persisted_products == [("Cardamom Roll", 7.25)]


def test_add_sales_product_rejects_existing_name(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    persisted_names = []
    monkeypatch.setattr(app_module, "persist_custom_bread_type", lambda name, price: persisted_names.append(name))
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.post(
        "/sales-log/add-product",
        data={"product_name": "pandesal", "product_price": "5.00"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "That product already exists."
    assert persisted_names == []


def test_add_sales_product_rejects_invalid_price_before_persisting(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    persisted_products = []
    monkeypatch.setattr(
        app_module,
        "persist_custom_bread_type",
        lambda name, price: persisted_products.append((name, price)),
    )
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.post(
        "/sales-log/add-product",
        data={"product_name": "Cardamom Roll", "product_price": "-2.00"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Enter a price from 0 to 99,999,999.99."
    assert persisted_products == []


def test_soft_delete_product_requires_csrf_and_hides_product(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "BREAD_TYPES", ["Pandesal", "Cardamom Roll"])
    monkeypatch.setattr(app_module, "BREAD_PRICES", {"Pandesal": 4.5, "Cardamom Roll": 7.25})
    monkeypatch.setattr(app_module, "INACTIVE_BREAD_TYPES", set())
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
        session["_csrf_token"] = "test-token"

    rejected = client.post("/products/delete/2")
    assert rejected.status_code == 400
    assert app_module.load_active_bread_types() == ["Pandesal", "Cardamom Roll"]

    response = client.post(
        "/products/delete/2",
        headers={"X-CSRF-Token": "test-token"},
    )

    assert response.status_code == 200
    assert response.get_json()["message"] == "Cardamom Roll deleted"
    assert app_module.load_active_bread_types() == ["Pandesal"]


def test_adding_deleted_product_reactivates_existing_runtime_name(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "BREAD_TYPES", ["Pandesal", "Cardamom Roll"])
    monkeypatch.setattr(app_module, "BREAD_PRICES", {"Pandesal": 4.5, "Cardamom Roll": 7.25})
    monkeypatch.setattr(app_module, "INACTIVE_BREAD_TYPES", {"Cardamom Roll"})
    monkeypatch.setattr(
        app_module,
        "persist_custom_bread_type",
        lambda name, price: (True, None, 23, True),
    )
    monkeypatch.setattr(app_module, "retrain_model_async", lambda sales: None)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.post(
        "/sales-log/add-product",
        data={"product_name": "Cardamom Roll", "product_price": "8.25"},
    )

    assert response.status_code == 200
    assert response.get_json()["reactivated"] is True
    assert response.get_json()["product_id"] == 23
    assert "Cardamom Roll" in app_module.load_active_bread_types()
    assert app_module.BREAD_TYPES.count("Cardamom Roll") == 1


def test_mysql_product_soft_delete_is_parameterized_and_preserves_sales(monkeypatch):
    class FakeCursor:
        rowcount = 1

        def __init__(self):
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append((query, params))

        def fetchone(self):
            return {"id": 42, "bread_type": "Cardamom Roll"}

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.fake_cursor = FakeCursor()
            self.committed = False

        def cursor(self, **kwargs):
            return self.fake_cursor

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("Unexpected rollback")

        def close(self):
            pass

    connection = FakeConnection()
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(app_module, "get_db_connection", lambda: connection)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
        session["_csrf_token"] = "test-token"

    response = client.post(
        "/products/delete/42",
        headers={"X-CSRF-Token": "test-token"},
    )

    assert response.status_code == 200
    assert connection.committed
    assert connection.fake_cursor.queries[0] == (
        "SELECT id, bread_type FROM bread_type_catalog "
        "WHERE id = %s AND is_active = 1 FOR UPDATE",
        (42,),
    )
    assert connection.fake_cursor.queries[1] == (
        "UPDATE bread_type_catalog SET is_active = 0 WHERE id = %s AND is_active = 1",
        (42,),
    )
    assert all("sales_data" not in query for query, _ in connection.fake_cursor.queries)


def test_sales_log_lists_active_products_and_keeps_deleted_sales_visible(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "BREAD_TYPES", ["Pandesal", "Old Roll"])
    monkeypatch.setattr(app_module, "BREAD_PRICES", {"Pandesal": 4.5, "Old Roll": 6.0})
    monkeypatch.setattr(app_module, "INACTIVE_BREAD_TYPES", {"Old Roll"})
    monkeypatch.setattr(app_module, "SALES_DATA", [{
        "id": 1,
        "date": "2026-10-09",
        "bread_type": "Old Roll",
        "products_produced": 10,
        "actual_qty_sold": 8,
        "waste_returns": 2,
        "price_per_product": 6.0,
        "expense_amount": 0.0,
    }])
    monkeypatch.setattr(app_module, "fetch_monthly_expense_total", lambda *args, **kwargs: 0)
    monkeypatch.setattr(app_module, "fetch_all_expenses_total", lambda *args, **kwargs: 0)
    monkeypatch.setattr(app_module, "fetch_monthly_expenses", lambda *args, **kwargs: {})
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.get("/sales-log")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="produced"' not in html
    assert 'name="sold"' in html
    assert 'value="Pandesal"' in html
    assert 'data-product-name="Old Roll"' not in html
    assert ">Old Roll</td>" in html
