Prediction System - Simple User Manual

Welcome to the Prediction System. This guide gives short, clear steps for the main features.

1. Start the app
- Install dependencies: `pip install -r requirements.txt`.
- Run locally: `python app.py`.
- Open your browser at `http://localhost:5000` (or your host URL).

2. Log in
- Go to the login page.
- Enter the password from environment variable `LOGIN_PASSWORD` (default: `chechebakeshop`).

3. Add sales records
- Open "Sales Log" or "Add Sale".
- Enter the date, bread type, produced, sold, price, and other fields.
- Save to add the record.

4. Upload sales CSV/Excel
- Go to the upload page.
- Choose your file (CSV/XLSX) and submit.
- The app normalizes and aggregates rows by date and bread type.

5. View predictions and production plan
- Visit "Predict" to run demand prediction for a date.
- Visit "Production Plan" to view saved predictions and suggested production quantities.

6. Manage ingredients
- Open "Ingredients Inventory" to view stock levels.
- Ingredient stock changes only when you use the manual Add stock or Deduct stock controls. Sales, production, and predictions do not adjust stock.
- Use Ingredient History to review opening balances and manual stock changes.

7. Save monthly/weekly/daily expenses
- Go to "Monthly Expenses".
- Choose scope (daily/weekly/monthly), enter amounts and notes for categories, then Save.
- If the app is hosted, check server logs when saves fail.

8. Model training and metrics
- The app trains a demand model from historical sales.
- View model performance in "Model Performance".

9. Troubleshooting
- If saving fails, check server logs for error messages.
- Confirm database settings in environment variables: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, and `USE_MYSQL`.

10. Deploying
- Use the provided `Procfile` or `gunicorn.conf.py` for production deployment.

Need more help? Describe the problem and include any server log lines.
