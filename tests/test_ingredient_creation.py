import app as app_module


def make_logged_in_client():
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
    return client


def test_add_ingredient_persists_to_in_memory_inventory(monkeypatch):
    ingredients = []
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "INGREDIENTS", ingredients)
    client = make_logged_in_client()

    response = client.post(
        "/ingredients/add",
        json={
            "name": "  Cocoa   Powder ",
            "current_stock": "12.5",
            "unit": "  kg ",
            "reorder_threshold": "3",
        },
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "success": True,
        "ingredient": {
            "id": 1,
            "name": "Cocoa Powder",
            "current_stock": 12.5,
            "unit": "kg",
            "reorder_threshold": 3.0,
            "last_restock": None,
        },
    }
    assert ingredients == [response.get_json()["ingredient"]]


def test_ingredients_page_builds_selector_from_loaded_ingredients(monkeypatch):
    ingredients = [
        {
            "id": 12,
            "name": "New Flour",
            "current_stock": 5.0,
            "unit": "kg",
            "reorder_threshold": 1.0,
            "last_restock": None,
        }
    ]
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "fetch_ingredients_from_db", lambda: ingredients)
    monkeypatch.setattr(app_module, "fetch_monthly_expenses", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "fetch_monthly_expense_total", lambda *args, **kwargs: 0.0)
    client = make_logged_in_client()

    response = client.get("/ingredients")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-ingredient-name="New Flour"' in html
    assert "All ingredients" in html
    assert "+ Add new ingredient" in html
    assert 'data-endpoint="/ingredients/add"' in html


def test_add_ingredient_rejects_duplicate_name_case_insensitively(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(
        app_module,
        "INGREDIENTS",
        [{"id": 8, "name": "Flour", "current_stock": 2, "unit": "kg", "reorder_threshold": 1}],
    )
    client = make_logged_in_client()

    response = client.post("/ingredients/add", json={"name": " flour "})

    assert response.status_code == 409
    assert response.get_json() == {"success": False, "error": "That ingredient already exists."}


def test_add_ingredient_rejects_invalid_stock_without_mutating_inventory(monkeypatch):
    ingredients = []
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "INGREDIENTS", ingredients)
    client = make_logged_in_client()

    response = client.post(
        "/ingredients/add",
        json={"name": "Cocoa Powder", "current_stock": -1, "reorder_threshold": 0},
    )

    assert response.status_code == 400
    assert ingredients == []


def test_soft_delete_requires_csrf_and_preserves_history(monkeypatch):
    ingredients = [
        {
            "id": 8,
            "name": "Flour",
            "current_stock": 4,
            "unit": "kg",
            "reorder_threshold": 1,
        }
    ]
    history = [{"ingredient_id": 8, "ingredient_name": "Flour", "action_type": "INITIAL"}]
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "INGREDIENTS", ingredients)
    monkeypatch.setattr(app_module, "INGREDIENT_HISTORY", history)
    client = make_logged_in_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-token"

    rejected = client.post("/ingredients/delete/8")
    assert rejected.status_code == 400
    assert ingredients[0].get("is_active", 1) == 1

    deleted = client.post(
        "/ingredients/delete/8",
        headers={"X-CSRF-Token": "test-token"},
    )

    assert deleted.status_code == 200
    assert deleted.get_json()["message"] == "Flour deleted"
    assert ingredients[0]["is_active"] == 0
    assert history == [{"ingredient_id": 8, "ingredient_name": "Flour", "action_type": "INITIAL"}]
    assert app_module.fetch_ingredients_from_db() == []


def test_adding_soft_deleted_ingredient_name_creates_new_row(monkeypatch):
    ingredients = [
        {
            "id": 8,
            "name": "Flour",
            "current_stock": 4,
            "unit": "kg",
            "reorder_threshold": 1,
            "is_active": 0,
        }
    ]
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "INGREDIENTS", ingredients)

    response = make_logged_in_client().post(
        "/ingredients/add",
        json={"name": "Flour", "current_stock": 2, "unit": "kg"},
    )

    assert response.status_code == 201
    assert response.get_json()["ingredient"]["id"] == 9
    assert len(ingredients) == 2
    assert ingredients[0]["is_active"] == 0
    assert ingredients[1].get("is_active", 1) == 1


def test_ingredients_page_does_not_reseed_soft_deleted_inventory(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(
        app_module,
        "INGREDIENTS",
        [{"id": 8, "name": "Flour", "current_stock": 4, "unit": "kg", "is_active": 0}],
    )
    monkeypatch.setattr(app_module, "fetch_monthly_expenses", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "fetch_monthly_expense_total", lambda *args, **kwargs: 0.0)

    response = make_logged_in_client().get("/ingredients")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="ing-row-8"' not in html
    assert "No active ingredients." in html
    assert app_module.fetch_ingredients_from_db() == []


def test_add_ingredient_inserts_into_mysql(monkeypatch):
    class FakeCursor:
        lastrowid = 42

        def __init__(self):
            self.queries = []
            self.closed = False

        def execute(self, query, params=None):
            self.queries.append((query, params))

        def fetchone(self):
            return None

        def close(self):
            self.closed = True

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.committed = False
            self.closed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("Unexpected rollback")

        def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(app_module, "get_db_connection", lambda: connection)
    client = make_logged_in_client()

    response = client.post(
        "/ingredients/add",
        json={"name": "Cocoa Powder", "current_stock": 4, "unit": "kg", "reorder_threshold": 1},
    )

    assert response.status_code == 201
    assert response.get_json()["ingredient"]["id"] == 42
    assert connection.committed is True
    assert connection.closed is True
    assert connection.cursor_instance.closed is True
    assert connection.cursor_instance.queries[2] == (
        "INSERT INTO ingredients (name, current_stock, unit, reorder_threshold) VALUES (%s, %s, %s, %s)",
        ("Cocoa Powder", 4.0, "kg", 1.0),
    )
    assert connection.cursor_instance.queries[3] == (
        """INSERT INTO ingredient_history (
               ingredient_id, ingredient_name, action_type, quantity_changed,
               unit, stock_before, stock_after, remarks
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (42, "Cocoa Powder", "INITIAL", app_module.Decimal("4.0000"), "kg",
         app_module.Decimal("0.0000"), app_module.Decimal("4.0000"), "Initial stock"),
    )
