import app as app_module


def test_dashboard_profit_uses_same_revenue_minus_expenses_as_sales_log(monkeypatch):
    sales = [{
        "date": "2026-01-01",
        "bread_type": "Pandesal",
        "products_produced": 12,
        "actual_qty_sold": 10,
        "waste_returns": 0,
        "price_per_product": 5.0,
    }]
    monkeypatch.setattr(app_module, "fetch_all_expenses_total", lambda: 20.0)
    monkeypatch.setattr(
        app_module,
        "sales_rows_with_batch_waste",
        lambda rows: [dict(row, waste_returns=2, waste_cost=45.0) for row in rows],
    )

    kpis = app_module.compute_kpis("2026-01-01", sales)

    assert kpis["totalSalesToday"] == 10
    assert kpis["totalRevenueToday"] == 50.0
    assert kpis["totalExpenseToday"] == 20.0
    assert kpis["totalProfitToday"] == 30.0
    assert kpis["avgDailyWaste"] == 2


def test_dashboard_monthly_profit_does_not_subtract_waste_cost(monkeypatch):
    sales = [{
        "date": "2026-01-01",
        "bread_type": "Pandesal",
        "products_produced": 12,
        "actual_qty_sold": 10,
        "waste_returns": 0,
        "price_per_product": 5.0,
    }]
    monkeypatch.setattr(
        app_module,
        "sales_rows_with_batch_waste",
        lambda rows: [dict(row, waste_returns=2, waste_cost=45.0) for row in rows],
    )
    monkeypatch.setattr(app_module, "fetch_monthly_expense_total", lambda month: 20.0)

    monthly = app_module.get_monthly_analytics(sales, months=1)

    assert monthly[0]["revenue"] == 50.0
    assert monthly[0]["expenses"] == 20.0
    assert monthly[0]["waste_cost"] == 45.0
    assert monthly[0]["profit"] == 30.0


def test_sales_log_summary_uses_full_dataset_when_filter_is_active(monkeypatch):
    app_module.app.config["TESTING"] = True
    app_module.SALES_DATA = [
        {
            "date": "2024-01-01",
            "bread_type": "Pandesal",
            "products_produced": 10,
            "actual_qty_sold": 5,
            "waste_returns": 1,
            "price_per_product": 10.0,
            "temperature": 30.0,
            "is_holiday": 0,
            "is_promotion": 0,
            "sacks_used": 0,
            "plates_used": 0,
            "expense_amount": 0.0,
        },
        {
            "date": "2024-01-02",
            "bread_type": "Cheese Bread",
            "products_produced": 20,
            "actual_qty_sold": 8,
            "waste_returns": 2,
            "price_per_product": 15.0,
            "temperature": 30.0,
            "is_holiday": 0,
            "is_promotion": 0,
            "sacks_used": 0,
            "plates_used": 0,
            "expense_amount": 0.0,
        },
    ]

    monkeypatch.setattr(app_module, "fetch_all_expenses_total", lambda: 0)
    monkeypatch.setattr(app_module, "fetch_monthly_expense_total", lambda month: 0)
    monkeypatch.setattr(app_module, "fetch_monthly_expenses", lambda month: [])
    monkeypatch.setattr(app_module, "retrain_model_async", lambda sales: None)
    app_module.WEEKLY_PLAN = []

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/prediction_system/sales-log?filter_date=2024-01-02&history_period=daily")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "₱170.00" in html
    assert "Dataset" in html and "2" in html
    assert '<td class="text-right">12</td>' in html
    assert "Total Profit" in html
    assert "Waste Cost" not in html
