"""Test retained bread recipe data and manual-only ingredient inventory behavior."""
import sys
import os
import unittest
from io import BytesIO

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as main_app


class TestRecipeDataManualOnly(unittest.TestCase):
    """Test recipe data independently from sales and ingredient stock changes."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Disable MySQL for testing
        os.environ['USE_MYSQL'] = '0'
        self.app = main_app.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
    
    def test_bread_recipes_defined(self):
        """Test that bread recipes are defined for major bread types."""
        self.assertIn('Pandesal', main_app.BREAD_RECIPES)
        self.assertIn('Cheese Bread', main_app.BREAD_RECIPES)
        self.assertIn('Ensaymada', main_app.BREAD_RECIPES)
        self.assertIn('Mamon', main_app.BREAD_RECIPES)
    
    def test_pandesal_recipe_has_ingredients(self):
        """Test that Pandesal recipe includes flour and other basics."""
        recipe = main_app.BREAD_RECIPES['Pandesal']
        self.assertIn('Flour (1st Class)', recipe)
        self.assertIn('Sugar', recipe)
        self.assertIn('Vegetable Oil', recipe)
        self.assertIn('Yeast', recipe)
        self.assertGreater(recipe['Flour (1st Class)'], 0)
    
    def test_recipe_quantities_are_positive(self):
        """Test that all recipe quantities are positive numbers."""
        for bread_type, ingredients in main_app.BREAD_RECIPES.items():
            for ingredient_name, quantity in ingredients.items():
                self.assertGreater(quantity, 0, 
                    f"Invalid quantity for {ingredient_name} in {bread_type}: {quantity}")
    
    def test_ingredient_calculation_for_sale(self):
        """Test ingredient calculation for a sales quantity."""
        recipe = main_app.BREAD_RECIPES['Pandesal']
        quantity_sold = 100
        
        # Calculate expected consumption
        flour_needed = recipe['Flour (1st Class)'] * quantity_sold
        self.assertGreater(flour_needed, 0)
        self.assertEqual(flour_needed, 8.0)  # 0.08 * 100
    
    def test_multiple_bread_recipes(self):
        """Test that multiple bread types have recipes."""
        count = len(main_app.BREAD_RECIPES)
        self.assertGreaterEqual(count, 5, 
            f"Expected at least 5 bread recipes, but got {count}")
    
    def test_cheese_bread_has_extra_ingredients(self):
        """Test that Cheese Bread recipe is different from Pandesal."""
        pandesal_recipe = main_app.BREAD_RECIPES['Pandesal']
        cheese_bread_recipe = main_app.BREAD_RECIPES['Cheese Bread']
        
        # Cheese bread should need more flour
        pandesal_flour = pandesal_recipe.get('Flour (1st Class)', 0)
        cheese_flour = cheese_bread_recipe.get('Flour (1st Class)', 0)
        self.assertGreater(cheese_flour, pandesal_flour,
            "Cheese Bread should require more flour than Pandesal")

    def test_sales_entry_upload_and_batch_do_not_change_ingredient_stock_or_history(self):
        ingredients = [
            {"id": 1, "name": "Flour (1st Class)", "current_stock": 50.0,
             "unit": "kg", "reorder_threshold": 5.0, "last_restock": None}
        ]
        history = []
        saved_records = []
        original_use_mysql = main_app.USE_MYSQL
        original_ingredients = main_app.INGREDIENTS
        original_history = main_app.INGREDIENT_HISTORY
        original_sales_data = main_app.SALES_DATA
        original_testing = main_app.app.config["TESTING"]
        main_app.USE_MYSQL = False
        main_app.INGREDIENTS = ingredients
        main_app.INGREDIENT_HISTORY = history
        main_app.SALES_DATA = []
        main_app.app.config["TESTING"] = True

        original_save = main_app.save_sales_records
        original_load = main_app.load_sales_data
        original_weekly = main_app.get_weekly_plan
        original_retrain = main_app.retrain_model_async
        original_parse_upload = main_app.parse_csv_upload
        original_save_historical = main_app.save_historical_sales_records
        original_save_cutoff = main_app.save_cutoff_sales_records

        def save_cutoff_records(records):
            saved_records.extend(records)
            return True, None

        def save_historical_records(records):
            saved_records.extend(records)
            return len(records), 0

        main_app.save_cutoff_sales_records = save_cutoff_records
        main_app.save_historical_sales_records = save_historical_records
        main_app.load_sales_data = lambda: []
        main_app.get_weekly_plan = lambda records: []
        main_app.retrain_model_async = lambda records: None
        main_app.parse_csv_upload = lambda uploaded: [{
            "date": "2026-10-11",
            "bread_type": "Pandesal",
            "products_produced": 100,
            "actual_qty_sold": 100,
            "waste_returns": 0,
            "price_per_product": 4.5,
            "temperature": 30.0,
            "is_holiday": 0,
            "is_peak day": 0,
            "sacks_used": 0,
            "plates_used": 0,
            "expense_amount": 0.0,
        }]
        try:
            client = main_app.app.test_client()
            with client.session_transaction() as session:
                session["logged_in"] = True

            response = client.post(
                "/sales-log",
                data={
                    "action": "add_record",
                    "date": "2026-10-11",
                    "bread_type": "Pandesal",
                    "produced": "100",
                    "sold": "100",
                    "price": "4.50",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(saved_records), 1)
            self.assertNotIn("products_produced", saved_records[0])
            self.assertEqual(ingredients[0]["current_stock"], 50.0)
            self.assertEqual(history, [])

            response = client.post(
                "/sales-log",
                data={
                    "action": "save_batch_records",
                    "date": "2026-10-11",
                    "scope_days": "1",
                    "bread_type": ["Pandesal"],
                    "entry_date": ["2026-10-11"],
                    "sold": ["100"],
                    "price": ["4.50"],
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(saved_records), 2)
            self.assertNotIn("products_produced", saved_records[1])
            self.assertEqual(ingredients[0]["current_stock"], 50.0)
            self.assertEqual(history, [])

            response = client.post(
                "/sales-log",
                data={
                    "action": "upload_csv",
                    "file": (BytesIO(b"test"), "sales.csv"),
                },
                content_type="multipart/form-data",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(saved_records), 3)
            self.assertNotIn("products_produced", saved_records[2])
            self.assertEqual(ingredients[0]["current_stock"], 50.0)
            self.assertEqual(history, [])
        finally:
            main_app.USE_MYSQL = original_use_mysql
            main_app.INGREDIENTS = original_ingredients
            main_app.INGREDIENT_HISTORY = original_history
            main_app.SALES_DATA = original_sales_data
            main_app.app.config["TESTING"] = original_testing
            main_app.save_cutoff_sales_records = original_save_cutoff
            main_app.load_sales_data = original_load
            main_app.get_weekly_plan = original_weekly
            main_app.retrain_model_async = original_retrain
            main_app.parse_csv_upload = original_parse_upload
            main_app.save_historical_sales_records = original_save_historical


if __name__ == '__main__':
    unittest.main()
