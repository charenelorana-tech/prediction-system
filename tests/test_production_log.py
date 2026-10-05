import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from unittest.mock import patch

import app as app_module
from app import app, build_production_log_rows


class ProductionLogHistoryTest(unittest.TestCase):
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

        rows = build_production_log_rows(prediction_rows, sales_rows)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bread_type"], "Pandesal")
        self.assertEqual(rows[0]["predicted_demand"], 120)
        self.assertEqual(rows[0]["recommended_production"], 132)
        self.assertEqual(rows[0]["actual_production"], 140)
        self.assertEqual(rows[0]["waste"], 15)
        self.assertEqual(rows[0]["confidence"], 95)

    def test_production_plan_page_renders_for_logged_in_user(self):
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["logged_in"] = True

        response = client.get("/production-plan")

        self.assertEqual(response.status_code, 200)

    def test_production_history_renders_editable_outcomes_and_print_ranges(self):
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
        ), patch.object(app_module, "fetch_production_plan_sessions", return_value=[]), patch.object(
            app_module, "fetch_production_plan_rows", return_value=[]
        ):
            response = client.get("/production-history")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-field="actual_production"', response.data)
        self.assertIn(b'data-field="waste"', response.data)
        self.assertIn(b'<option value="daily">Daily</option>', response.data)
        self.assertIn(b'<option value="weekly">Weekly</option>', response.data)
        self.assertIn(b'<option value="monthly">Monthly</option>', response.data)
        self.assertIn(b'<option value="custom">Custom range</option>', response.data)

    def test_production_outcome_update_validates_and_saves_field(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with patch.object(app_module, "save_production_outcome", return_value=(True, None)) as save:
            response = client.post(
                "/production-plan/update-outcome",
                data={
                    "date": "2026-06-01",
                    "bread_type": "Pandesal",
                    "field": "actual_production",
                    "value": "140",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "field": "actual_production", "value": 140})
        save.assert_called_once_with("2026-06-01", "Pandesal", "actual_production", 140)

    def test_production_outcome_update_rejects_negative_and_invalid_fields(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with patch.object(app_module, "save_production_outcome") as save:
            negative = client.post(
                "/production-plan/update-outcome",
                data={"date": "2026-06-01", "bread_type": "Pandesal", "field": "waste", "value": "-1"},
            )
            invalid_field = client.post(
                "/production-plan/update-outcome",
                data={"date": "2026-06-01", "bread_type": "Pandesal", "field": "price", "value": "1"},
            )

        self.assertEqual(negative.status_code, 400)
        self.assertEqual(invalid_field.status_code, 400)
        save.assert_not_called()

    def test_save_production_outcome_upserts_only_requested_database_column(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value

        with patch.object(app_module, "USE_MYSQL", True), patch.object(
            app_module, "get_db_connection", return_value=connection
        ), patch.object(app_module, "SALES_DATA", []):
            saved, error = app_module.save_production_outcome(
                "2026-06-01", "Pandesal", "waste", 9
            )

        self.assertTrue(saved)
        self.assertIsNone(error)
        query, params = cursor.execute.call_args.args
        self.assertIn("ON DUPLICATE KEY UPDATE waste_returns = VALUES(waste_returns)", query)
        self.assertEqual(params, ("2026-06-01", "Pandesal", 0, 9))
        connection.commit.assert_called_once()

    def test_save_actual_production_uses_production_column(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value

        with patch.object(app_module, "USE_MYSQL", True), patch.object(
            app_module, "get_db_connection", return_value=connection
        ), patch.object(app_module, "SALES_DATA", []):
            saved, error = app_module.save_production_outcome(
                "2026-06-01", "Pandesal", "actual_production", 140
            )

        self.assertTrue(saved)
        self.assertIsNone(error)
        query, params = cursor.execute.call_args.args
        self.assertIn("ON DUPLICATE KEY UPDATE products_produced = VALUES(products_produced)", query)
        self.assertEqual(params, ("2026-06-01", "Pandesal", 140, 0))
        connection.commit.assert_called_once()

    def test_save_production_outcome_creates_missing_in_memory_record(self):
        sales_data = []
        with patch.object(app_module, "USE_MYSQL", False), patch.object(app_module, "SALES_DATA", sales_data):
            saved, error = app_module.save_production_outcome(
                "2026-06-01", "Pandesal", "waste", 9
            )

        self.assertTrue(saved)
        self.assertIsNone(error)
        self.assertEqual(sales_data[0]["products_produced"], 0)
        self.assertEqual(sales_data[0]["waste_returns"], 9)


if __name__ == "__main__":
    unittest.main()
