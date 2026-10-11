import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from unittest.mock import patch

import app as app_module
from app import app, build_production_log_rows


class ProductionLogHistoryTest(unittest.TestCase):
    def test_production_date_bounds_use_monday_start_and_calendar_month(self):
        today = date(2026, 10, 9)

        self.assertEqual(
            app_module.production_date_bounds("week", today=today),
            (date(2026, 10, 5), date(2026, 10, 11)),
        )
        self.assertEqual(
            app_module.production_date_bounds("month", today=today),
            (date(2026, 10, 1), date(2026, 10, 31)),
        )

    def test_custom_production_date_bounds_validate_order(self):
        self.assertEqual(
            app_module.production_date_bounds(
                "custom", "2026-10-02", "2026-10-09"
            ),
            (date(2026, 10, 2), date(2026, 10, 9)),
        )
        with self.assertRaises(ValueError):
            app_module.production_date_bounds(
                "custom", "2026-10-10", "2026-10-09"
            )

    def test_production_filter_and_summary_apply_together(self):
        rows = [
            {
                "date": "2026-10-08",
                "bread_type": "Pandesal",
                "recommended_production": 100,
                "actual_production": 90,
                "sold": 80,
                "leftover": 5,
                "waste": 5,
                "waste_cost": 22.5,
            },
            {
                "date": "2026-10-07",
                "bread_type": "Biscutcho",
                "recommended_production": 20,
                "actual_production": 18,
                "sold": 15,
                "leftover": 3,
                "waste": 0,
                "waste_cost": 0,
            },
            {
                "date": "2026-09-30",
                "bread_type": "Pandesal",
                "recommended_production": 50,
                "actual_production": 45,
                "sold": 40,
                "leftover": 5,
                "waste": 0,
                "waste_cost": 0,
            },
        ]
        filtered = app_module.filter_production_log_rows(
            rows, "Pandesal", date(2026, 10, 1), date(2026, 10, 31)
        )

        self.assertEqual([row["date"] for row in filtered], ["2026-10-08"])
        self.assertEqual(
            app_module.production_log_summary(filtered),
            {
                "recommended": 100,
                "actual": 90,
                "sold": 80,
                "leftover": 5,
                "waste": 5,
                "waste_cost": 22.5,
            },
        )

    def test_filtered_production_keys_use_parameterized_sql(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [(date(2026, 10, 8), "Pandesal")]
        with patch.object(app_module, "USE_MYSQL", True), patch.object(
            app_module, "get_db_connection", return_value=connection
        ):
            keys = app_module.fetch_production_record_keys(
                "Pandesal", date(2026, 10, 5), date(2026, 10, 11)
            )

        query, params = cursor.execute.call_args.args
        self.assertIn("date BETWEEN %s AND %s", query)
        self.assertIn("date_predicted BETWEEN %s AND %s", query)
        self.assertIn("bread_type = %s", query)
        self.assertEqual(
            params,
            (
                date(2026, 10, 5), date(2026, 10, 11), "Pandesal",
                date(2026, 10, 5), date(2026, 10, 11), "Pandesal",
            ),
        )
        self.assertEqual(keys, {("2026-10-08", "Pandesal")})

    def test_build_production_log_rows_links_predictions_to_sales_outcomes(self):
        prediction_rows = [
            {
                "bread_type": "Pandesal",
                "date_predicted": date(2026, 6, 1),
                "predicted_demand": 120,
                "recommended_production": 132,
                "confidence": 95,
            }
        ]
        sales_rows = [
            {
                "date": "2026-06-01",
                "bread_type": "Pandesal",
                "products_produced": 140,
                "actual_qty_sold": 125,
                "waste_returns": 15,
            }
        ]

        with patch.object(app_module, "_load_shelf_life_days", return_value={}), patch.object(
            app_module, "_load_product_prices", return_value={}
        ):
            rows = build_production_log_rows(
                prediction_rows, sales_rows, as_of=date(2026, 6, 3)
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bread_type"], "Pandesal")
        self.assertEqual(rows[0]["predicted_demand"], 120)
        self.assertEqual(rows[0]["recommended_production"], 132)
        self.assertEqual(rows[0]["actual_production"], 140)
        self.assertEqual(rows[0]["sold"], 125)
        self.assertEqual(rows[0]["leftover"], 15)
        self.assertEqual(rows[0]["waste"], 0)
        self.assertEqual(rows[0]["confidence"], 95)

    def test_cutoff_production_rows_derive_waste_and_mark_unentered_production(self):
        sales = [
            {
                "date": "2026-10-11",
                "bread_type": "Pandesal",
                "products_produced": 20,
                "actual_qty_sold": 15,
                "price_per_product": 4.5,
            },
            {
                "date": "2026-10-12",
                "bread_type": "Pandesal",
                "products_produced": 0,
                "actual_qty_sold": 0,
                "price_per_product": 4.5,
            },
        ]
        with patch.object(app_module, "_load_shelf_life_days", return_value={}), patch.object(
            app_module, "_load_product_prices", return_value={"Pandesal": 4.5}
        ):
            rows = build_production_log_rows([], sales, as_of=date(2026, 10, 13))

        rows_by_date = {row["date"]: row for row in rows}
        self.assertEqual(rows_by_date["2026-10-11"]["sold"], 15)
        self.assertEqual(rows_by_date["2026-10-11"]["waste"], 5)
        self.assertFalse(rows_by_date["2026-10-11"]["waste_pending"])
        self.assertEqual(rows_by_date["2026-10-12"]["waste"], 0)
        self.assertTrue(rows_by_date["2026-10-12"]["waste_pending"])

    def test_production_outcome_allows_historical_dates_and_rejects_sales_over_production(self):
        with patch.object(app_module, "USE_MYSQL", False), patch.object(
            app_module, "SALES_DATA",
            [{
                "date": "2026-10-11",
                "bread_type": "Pandesal",
                "products_produced": 8,
                "actual_qty_sold": 5,
            }],
        ):
            historical_saved, historical_error = app_module.save_production_outcome(
                "2026-10-10", "Pandesal", "actual_production", 8
            )
            lower_saved, lower_error = app_module.save_production_outcome(
                "2026-10-11", "Pandesal", "actual_production", 0
            )

        self.assertTrue(historical_saved)
        self.assertIsNone(historical_error)
        self.assertFalse(lower_saved)
        self.assertIn("exceeds actual production", lower_error)

    def test_historical_sales_are_not_shown_as_production_log_rows(self):
        with patch.object(app_module, "_load_shelf_life_days", return_value={}), patch.object(
            app_module, "_load_product_prices", return_value={}
        ):
            rows = build_production_log_rows(
                [],
                [{
                    "date": "2026-03-01",
                    "bread_type": "Ube Pandesal",
                    "products_produced": 0,
                    "actual_qty_sold": 214,
                    "is_historical": 1,
                }],
                as_of=date(2026, 3, 2),
            )

        self.assertEqual(rows, [])

    def test_production_plan_page_renders_for_logged_in_user(self):
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with patch.object(app_module, "fetch_prediction_records", return_value=[]), patch.object(
            app_module, "SALES_DATA", []
        ), patch.object(app_module, "USE_MYSQL", False), patch.object(
            app_module, "_load_shelf_life_days", return_value={}
        ), patch.object(
            app_module, "_load_product_prices", return_value={}
        ):
            response = client.get("/production-plan")

        self.assertEqual(response.status_code, 200)

    def test_production_plan_page_filters_and_renders_summary(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        today = app_module.datetime.now(app_module.MANILA_TZ).date()
        day = today.isoformat()
        prediction = {
            "bread_type": "Pandesal",
            "date_predicted": day,
            "predicted_demand": 18,
            "recommended_production": 20,
            "confidence": 95,
        }
        sales = [{
            "date": day,
            "bread_type": "Pandesal",
            "products_produced": 20,
            "actual_qty_sold": 15,
            "waste_returns": 0,
            "price_per_product": 4.5,
        }]
        with patch.object(app_module, "USE_MYSQL", False), patch.object(
            app_module, "load_active_product_rows",
            return_value=[{"id": 1, "bread_type": "Pandesal", "default_price": 4.5}],
        ), patch.object(
            app_module, "fetch_prediction_records", return_value=[prediction]
        ), patch.object(
            app_module, "SALES_DATA", sales
        ), patch.object(app_module, "_load_shelf_life_days", return_value={}), patch.object(
            app_module, "_load_product_prices", return_value={"Pandesal": 4.5}
        ):
            response = client.get(
                f"/production-plan?bread=Pandesal&range=custom&start={day}&end={day}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="Pandesal" data-selected="1"', response.data)
        self.assertIn(b'value="custom" selected', response.data)
        self.assertIn(b"Showing 1 records", response.data)
        self.assertIn(b"Total recommended production", response.data)
        self.assertIn(b">20</strong>", response.data)
        self.assertIn(b"Leftover</th>", response.data)
        self.assertIn(b'data-field="actual_production"', response.data)

    def test_pre_cutoff_production_plan_actual_is_editable(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        prediction = {
            "bread_type": "Pandesal",
            "date_predicted": "2026-10-10",
            "predicted_demand": 18,
            "recommended_production": 20,
            "confidence": 95,
        }
        sales = [{
            "date": "2026-10-10",
            "bread_type": "Pandesal",
            "products_produced": 20,
            "actual_qty_sold": 15,
            "price_per_product": 4.5,
        }]
        with patch.object(app_module, "USE_MYSQL", False), patch.object(
            app_module, "load_active_product_rows",
            return_value=[{"id": 1, "bread_type": "Pandesal", "default_price": 4.5}],
        ), patch.object(
            app_module, "fetch_prediction_records", return_value=[prediction]
        ), patch.object(
            app_module, "SALES_DATA", sales
        ), patch.object(app_module, "_load_shelf_life_days", return_value={}), patch.object(
            app_module, "_load_product_prices", return_value={"Pandesal": 4.5}
        ):
            response = client.get(
                "/production-plan?bread=Pandesal&range=custom&start=2026-10-10&end=2026-10-10"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-field="actual_production"', response.data)
        self.assertIn(b'value="20"', response.data)

    def test_cutoff_entry_flow_saves_production_then_sold_and_displays_waste(self):
        client = app.test_client()
        sales_rows = []
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        with patch.object(app_module, "USE_MYSQL", False), patch.object(
            app_module, "SALES_DATA", sales_rows
        ), patch.object(app_module, "load_sales_data", lambda: sales_rows), patch.object(
            app_module, "get_weekly_plan", return_value=[]
        ), patch.object(app_module, "retrain_model_async"), patch.object(
            app_module, "fetch_prediction_records", return_value=[]
        ), patch.object(
            app_module, "load_active_product_rows",
            return_value=[{"id": 1, "bread_type": "Pandesal", "default_price": 4.5}],
        ), patch.object(
            app_module, "_load_shelf_life_days", return_value={}
        ), patch.object(
            app_module, "_load_product_prices", return_value={"Pandesal": 4.5}
        ), patch.object(
            app_module, "fetch_monthly_expense_total", return_value=0
        ), patch.object(
            app_module, "fetch_all_expenses_total", return_value=0
        ), patch.object(
            app_module, "fetch_monthly_expenses", return_value={}
        ):
            production = client.post(
                "/production-plan/update-outcome",
                data={
                    "date": "2026-10-11",
                    "bread_type": "Pandesal",
                    "field": "actual_production",
                    "value": "10",
                },
            )
            self.assertEqual(production.status_code, 200)

            sale = client.post(
                "/sales-log",
                data={
                    "action": "save_batch_records",
                    "date": "2026-10-11",
                    "scope_days": "1",
                    "bread_type": ["Pandesal"],
                    "entry_date": ["2026-10-11"],
                    "sold": ["6"],
                    "price": ["4.50"],
                },
            )
            self.assertEqual(sale.status_code, 200)
            self.assertEqual(sales_rows[0]["products_produced"], 10)
            self.assertEqual(sales_rows[0]["actual_qty_sold"], 6)

            plan = client.get(
                "/production-plan?bread=Pandesal&range=custom&start=2026-10-11&end=2026-10-11"
            )
            self.assertEqual(plan.status_code, 200)
            self.assertIn(b'data-derived="sold">6</td>', plan.data)
            self.assertIn(b'data-derived="waste"', plan.data)
            self.assertIn(b">4 <small>", plan.data)

            rejected_sale = client.post(
                "/sales-log",
                data={
                    "action": "save_batch_records",
                    "date": "2026-10-11",
                    "scope_days": "1",
                    "bread_type": ["Pandesal"],
                    "entry_date": ["2026-10-11"],
                    "sold": ["11"],
                    "price": ["4.50"],
                },
            )

        self.assertEqual(rejected_sale.status_code, 200)
        self.assertEqual(sales_rows[0]["actual_qty_sold"], 6)

    def test_production_history_renders_read_only_outcomes_and_print_ranges(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        predictions = [{
            "bread_type": "Pandesal",
            "date_predicted": "2026-06-01",
            "predicted_demand": 120,
            "recommended_production": 132,
            "confidence": 95,
        }]
        sales = [{
            "date": "2026-06-01",
            "bread_type": "Pandesal",
            "products_produced": 140,
            "actual_qty_sold": 125,
            "waste_returns": 15,
        }]
        with patch.object(app_module, "fetch_prediction_records", return_value=predictions), patch.object(
            app_module, "SALES_DATA", sales
        ), patch.object(app_module, "USE_MYSQL", False), patch.object(
            app_module, "fetch_production_plan_sessions", return_value=[]
        ), patch.object(
            app_module, "fetch_production_plan_rows", return_value=[]
        ), patch.object(app_module, "_load_shelf_life_days", return_value={}), patch.object(
            app_module, "_load_product_prices", return_value={}
        ):
            response = client.get("/production-history")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'data-field="actual_production"', response.data)
        self.assertNotIn(b'data-field="waste"', response.data)
        self.assertIn(b"<th>Leftover</th>", response.data)
        self.assertNotIn(b"<th>Confidence</th>", response.data)
        self.assertIn(b'<option value="daily">Daily</option>', response.data)
        self.assertIn(b'<option value="weekly">Weekly</option>', response.data)
        self.assertIn(b'<option value="monthly">Monthly</option>', response.data)
        self.assertIn(b'<option value="custom">Custom range</option>', response.data)

    def test_production_outcome_update_validates_and_saves_field(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with patch.object(app_module, "save_production_outcome", return_value=(True, None)) as save, patch.object(
            app_module, "SALES_DATA", []
        ), patch.object(app_module, "_load_shelf_life_days", return_value={}), patch.object(
            app_module, "_load_product_prices", return_value={}
        ):
            response = client.post(
                "/production-plan/update-outcome",
                data={
                    "date": "2026-10-11",
                    "bread_type": "Pandesal",
                    "field": "actual_production",
                    "value": "140",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["success"], True)
        self.assertEqual(response.get_json()["field"], "actual_production")
        self.assertEqual(response.get_json()["value"], 140)
        save.assert_called_once_with(
            "2026-10-11", "Pandesal", "actual_production", 140,
            allow_overstock=False,
        )

    def test_production_outcome_update_rejects_negative_and_invalid_fields(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with patch.object(app_module, "save_production_outcome") as save:
            negative = client.post(
                "/production-plan/update-outcome",
                data={"date": "2026-10-11", "bread_type": "Pandesal", "field": "actual_production", "value": "-1"},
            )
            invalid_field = client.post(
                "/production-plan/update-outcome",
                data={"date": "2026-10-11", "bread_type": "Pandesal", "field": "price", "value": "1"},
            )

        self.assertEqual(negative.status_code, 400)
        self.assertEqual(invalid_field.status_code, 400)
        save.assert_not_called()

    def test_save_production_outcome_upserts_actual_without_rebuilding_fifo_batches(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with patch.object(app_module, "USE_MYSQL", True), patch.object(
            app_module, "get_db_connection", return_value=connection
        ), patch.object(app_module, "SALES_DATA", []):
            saved, error = app_module.save_production_outcome(
                "2026-10-11", "Pandesal", "actual_production", 9
            )

        self.assertTrue(saved)
        self.assertIsNone(error)
        queries = [call.args for call in cursor.execute.call_args_list]
        upsert = next(args for args in queries if "ON DUPLICATE KEY UPDATE products_produced" in args[0])
        self.assertEqual(upsert[1], ("2026-10-11", "Pandesal", 9))
        self.assertFalse(any("DELETE FROM production_batches" in args[0] for args in queries))
        self.assertFalse(any("DELETE FROM sales_batch_allocations" in args[0] for args in queries))
        connection.commit.assert_called_once()

    def test_save_actual_production_uses_production_column(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with patch.object(app_module, "USE_MYSQL", True), patch.object(
            app_module, "get_db_connection", return_value=connection
        ), patch.object(app_module, "SALES_DATA", []):
            saved, error = app_module.save_production_outcome(
                "2026-10-11", "Pandesal", "actual_production", 140
            )

        self.assertTrue(saved)
        self.assertIsNone(error)
        query, params = next(
            args for args in (call.args for call in cursor.execute.call_args_list)
            if "ON DUPLICATE KEY UPDATE products_produced" in args[0]
        )
        self.assertIn("ON DUPLICATE KEY UPDATE products_produced = VALUES(products_produced)", query)
        self.assertEqual(params, ("2026-10-11", "Pandesal", 140))
        connection.commit.assert_called_once()

    def test_save_production_outcome_creates_missing_in_memory_record(self):
        sales_data = []
        with patch.object(app_module, "USE_MYSQL", False), patch.object(app_module, "SALES_DATA", sales_data):
            saved, error = app_module.save_production_outcome(
                "2026-10-11", "Pandesal", "actual_production", 9
            )

        self.assertTrue(saved)
        self.assertIsNone(error)
        self.assertEqual(sales_data[0]["products_produced"], 9)
        self.assertEqual(sales_data[0]["waste_returns"], 0)


if __name__ == "__main__":
    unittest.main()
