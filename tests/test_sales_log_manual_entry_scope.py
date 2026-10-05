from datetime import date

import app as app_module
from app import build_batch_entry_records


def test_build_batch_entry_records_respects_selected_date_scope():
    rows = build_batch_entry_records(
        start_date=date(2026, 6, 2),
        scope_days=3,
        bread_inputs=[
            {"bread_type": "Pandesal", "produced": 10, "sold": 7, "price": 4.5},
            {"bread_type": "Cheese Bread", "produced": 4, "sold": 3, "price": 5.5},
        ],
    )

    assert len(rows) == 6
    assert rows[0]["date"] == "2026-06-02"
    assert rows[0]["bread_type"] == "Pandesal"
    assert rows[1]["date"] == "2026-06-02"
    assert rows[1]["bread_type"] == "Cheese Bread"
    assert rows[2]["date"] == "2026-06-03"
    assert rows[2]["bread_type"] == "Pandesal"
    assert rows[5]["date"] == "2026-06-04"
    assert rows[5]["bread_type"] == "Cheese Bread"


def test_add_sales_product_persists_and_updates_runtime_catalog(monkeypatch):
    monkeypatch.setattr(app_module, "BREAD_TYPES", list(app_module.BREAD_TYPES))
    monkeypatch.setattr(app_module, "BREAD_PRICES", dict(app_module.BREAD_PRICES))
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
    assert response.get_json() == {"success": True, "product": "Cardamom Roll", "price": 7.25}
    assert "Cardamom Roll" in app_module.BREAD_TYPES
    assert app_module.BREAD_PRICES["Cardamom Roll"] == 7.25
    assert persisted_products == [("Cardamom Roll", 7.25)]


def test_add_sales_product_rejects_existing_name(monkeypatch):
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
