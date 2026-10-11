import io

import app as app_module


class FakeHistoricalCursor:
    def __init__(self, existing_keys=(), fail_on_raw_insert=False):
        self.existing_keys = set(existing_keys)
        self.fail_on_raw_insert = fail_on_raw_insert
        self.pending_row = None
        self.statements = []
        self.closed = False

    def execute(self, query, params=()):
        normalized_query = " ".join(query.split())
        self.statements.append((normalized_query, params))
        if normalized_query.startswith("SELECT id FROM sales_data"):
            self.pending_row = (
                {"id": 1}
                if (str(params[0]), params[1]) in self.existing_keys
                else None
            )
        elif normalized_query.startswith("INSERT INTO raw_sales_uploads"):
            if self.fail_on_raw_insert:
                raise RuntimeError("simulated raw upload insert failure")

    def fetchone(self):
        row = self.pending_row
        self.pending_row = None
        return row

    def close(self):
        self.closed = True


class FakeHistoricalConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.started = 0
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def start_transaction(self):
        self.started += 1

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def test_sales_log_upload_rejects_pre_cutoff_rows_without_saving(monkeypatch):
    imported_rows = [{
        "date": "2026-01-01",
        "bread_type": "Pandesal",
        "products_produced": 0,
        "actual_qty_sold": 8,
        "is_historical": 1,
    }]
    saved_rows = []
    retrained_rows = []
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(app_module, "SALES_DATA", [])
    monkeypatch.setattr(app_module, "load_sales_data", lambda: imported_rows)
    monkeypatch.setattr(app_module, "get_weekly_plan", lambda rows: [])
    monkeypatch.setattr(app_module, "retrain_model_async", retrained_rows.append)
    monkeypatch.setattr(
        app_module,
        "save_historical_sales_records",
        lambda rows: (saved_rows.extend(rows) or (1, 0)),
    )
    monkeypatch.setattr(
        app_module,
        "save_sales_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Historical uploads must not use the manual sales saver.")
        ),
    )
    monkeypatch.setattr(
        app_module,
        "sales_overstock_warnings",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Historical uploads must not check available stock.")
        ),
    )

    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
    response = client.post(
        "/sales-log",
        data={
            "action": "upload_csv",
            "file": (
                io.BytesIO(
                    b"sale_date,bread_type,actual_qty_sold\n"
                    b"1/1/2026,Pandesal,8\n"
                ),
                "historical.csv",
            ),
        },
        headers={"Accept": "application/json"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "before 2026-10-11 are read-only" in response.get_json()["error"]
    assert saved_rows == []
    assert retrained_rows == []


def test_sales_log_upload_returns_row_and_raw_date_for_invalid_date(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", False)
    monkeypatch.setattr(app_module, "SALES_DATA", [])
    monkeypatch.setattr(
        app_module,
        "save_historical_sales_records",
        lambda rows: (_ for _ in ()).throw(
            AssertionError("Invalid-date uploads must not be saved.")
        ),
    )
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.post(
        "/sales-log",
        data={
            "action": "upload_excel",
            "file": (
                io.BytesIO(
                    b"Date,bread_type,actual_qty_sold\n"
                    b"not-a-date,Pandesal,8\n"
                ),
                "invalid-date.csv",
            ),
        },
        headers={"Accept": "application/json"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "Record 2 has a missing or invalid date: 'not-a-date'."
    )


def test_historical_sales_saver_rejects_pre_cutoff_rows_without_database_writes(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(
            AssertionError("Pre-cutoff imports must not contact the database.")
        ),
    )

    try:
        app_module.save_historical_sales_records([{
            "date": "2026-01-02",
            "bread_type": "Monggo Bread",
            "actual_qty_sold": 25,
        }])
    except ValueError as err:
        assert "before 2026-10-11 are read-only" in str(err)
    else:
        raise AssertionError("Expected the pre-cutoff import to be rejected.")


def test_historical_sales_saver_accepts_cutoff_date(monkeypatch):
    cursor = FakeHistoricalCursor()
    connection = FakeHistoricalConnection(cursor)
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(app_module, "get_db_connection", lambda: connection)

    imported, skipped = app_module.save_historical_sales_records([{
        "date": "2026-10-11",
        "bread_type": "Monggo Bread",
        "actual_qty_sold": 25,
    }])

    assert (imported, skipped) == (1, 0)
    assert connection.committed == 1
    assert connection.rolled_back == 0
