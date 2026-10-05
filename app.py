import csv
import io
import json
import os
import random
import math
from datetime import date, datetime, timedelta
from functools import wraps

import mysql.connector
import numpy as np
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
import logging

# Main application:
# This Flask app manages bakery sales records, trains a demand prediction model,
# generates weekly production logs, and displays dashboards for performance.
# It uses MySQL when enabled, with a fallback to JSON data loading.
# The routes support login, sales logging, prediction, production logging, and model metrics.
# Require XGBoost for training and prediction
try:
    from xgboost import XGBRegressor
    USE_XGBOOST = True
    print("Using XGBoost for ML predictions")
except ImportError as err:
    raise ImportError(
        "XGBoost is required for this system. Install the xgboost package before running the app."
    ) from err

# Application prefix and Flask setup
# The app can be mounted under a subpath like /prediction_system, but in local
# root-hosted previews we default to the site root unless an explicit prefix is
# provided through APP_PREFIX. We still keep a compatibility prefix at the WSGI
# layer so older /prediction_system links continue to resolve safely.
DEFAULT_APP_PREFIX = ""
COMPAT_PREFIX = "/prediction_system"

def resolve_app_prefix(raw_prefix=None):
    prefix = raw_prefix if raw_prefix is not None else os.environ.get("APP_PREFIX", DEFAULT_APP_PREFIX)
    if not prefix or prefix in ("/", "\\"):
        return ""
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/")


PREFIX = resolve_app_prefix()
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path=f"{PREFIX}/static",
)
app.config["APPLICATION_ROOT"] = ""
app.config["DEBUG"] = os.environ.get("FLASK_DEBUG", "1").strip().lower() in ("1", "true", "yes", "on")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD", "chechebakeshop").strip()

if PREFIX:
    app.static_url_path = f"{PREFIX}/static"
else:
    app.static_url_path = "/static"

# Logging
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

@app.context_processor
def inject_prefix():
    # Make the mount prefix available to all Jinja templates so links include it when needed
    return {"PREFIX": PREFIX}

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "src", "data", "initial_data.json")

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", 3306))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "prediction_system")
USE_MYSQL = os.environ.get("USE_MYSQL", "1").strip().lower() in ("1", "true", "yes", "on")

BREAD_TYPES = [
    'Ube Pandesal', 'Cheese Bread', 'Ensaymada', 'Mamon', 'Star Bread',
    'Pandesal', 'Stick Bread', 'Monggo Bread', 'Biscutcho', 'Loaf Bread',
    'Teren', 'Burger Buns', 'Fita Favorita', 'Ugoy-Ugoy', 'Pandeleche',
    'Sambag-Sambag', 'Munay', 'Cookies', 'Radio-Radio', 'Panchu',
    'Pan', 'Sliced Pan (Katalogan)', 'Panches'
]
BREAD_PRICES = {bread_type: 4.5 for bread_type in BREAD_TYPES}

# Bread recipes: ingredient_name -> quantity per unit produced
BREAD_RECIPES = {
    'Pandesal': {
        'Flour (1st Class)': 0.08,
        'Sugar': 0.005,
        'Vegetable Oil': 0.003,
        'Margarine': 0.002,
        'Yeast': 0.001,
        'Salt': 0.0005,
        'Baking Powder': 0.0005,
    },
    'Ube Pandesal': {
        'Flour (1st Class)': 0.08,
        'Sugar': 0.006,
        'Vegetable Oil': 0.003,
        'Margarine': 0.002,
        'Yeast': 0.001,
        'Salt': 0.0005,
        'Baking Powder': 0.0005,
    },
    'Cheese Bread': {
        'Flour (1st Class)': 0.1,
        'Sugar': 0.007,
        'Vegetable Oil': 0.004,
        'Margarine': 0.003,
        'Yeast': 0.0015,
        'Salt': 0.0005,
        'Baking Powder': 0.0008,
    },
    'Ensaymada': {
        'Flour (1st Class)': 0.12,
        'Sugar': 0.015,
        'Vegetable Oil': 0.005,
        'Margarine': 0.008,
        'Lard': 0.004,
        'Yeast': 0.002,
        'Salt': 0.0008,
    },
    'Mamon': {
        'Flour (3rd Class)': 0.06,
        'Sugar': 0.04,
        'Vegetable Oil': 0.025,
        'Baking Powder': 0.005,
        'Salt': 0.0005,
    },
    'Burger Buns': {
        'Flour (1st Class)': 0.15,
        'Sugar': 0.01,
        'Vegetable Oil': 0.005,
        'Margarine': 0.004,
        'Yeast': 0.002,
        'Salt': 0.0008,
        'Baking Powder': 0.001,
    },
}

EXPENSE_CATEGORIES = [
    'Flour (1st Class)',
    'Flour (3rd Class)',
    'Sugar',
    'Vegetable Oil',
    'Margarine',
    'Lard',
    'Butter Milk',
    'Yeast',
    'Salt',
    'Baking Powder',
    'Anti-Amag',
    'Amoniaco'
]

# Default ingredients with units for fallback display when DB empty
DEFAULT_INGREDIENTS = [
    {"name": "Flour (1st Class)", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Flour (3rd Class)", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Sugar", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Vegetable Oil", "unit": "container", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Margarine", "unit": "tubs(40kgs)", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Lard", "unit": "tubs(40kgs)", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Butter Milk", "unit": "bags", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Yeast", "unit": "kgs", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Salt", "unit": "sack", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Baking Powder", "unit": "bags", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Anti-Amag", "unit": "kgs", "current_stock": 0.0, "reorder_threshold": 0.0},
    {"name": "Amoniaco", "unit": "kgs", "current_stock": 0.0, "reorder_threshold": 0.0},
]

# In-memory application state used during requests.
# SALES_DATA is the loaded sales dataset. MODEL stores the trained ML model.
# WEEKLY_PLAN and MODEL_METRICS are cached values for display in the dashboard.
WEEKLY_PLAN = []
MODEL_METRICS = {}
MODEL = None
SALES_DATA = []
INGREDIENTS = []
MONTHLY_EXPENSES = {}


class PrefixMiddleware:
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith(self.prefix):
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + self.prefix
            environ["PATH_INFO"] = path[len(self.prefix):]
            if environ["PATH_INFO"] == "":
                environ["PATH_INFO"] = "/"
            return self.app(environ, start_response)
        # If the request does not include the mount prefix, pass through
        # unchanged so the app can be accessed at the root during tests
        # or when served without a mount point.
        return self.app(environ, start_response)


def parse_date(value):
    """Convert a string or date-like value to a datetime.date object."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        parts = [p.strip() for p in str(value).split("/")]
        if len(parts) == 3:
            month, day, year = parts
            if len(year) == 2:
                year = f"20{year}"
            return date(int(year), int(month), int(day))
    except Exception:
        pass
    return None


def format_date(value):
    d = parse_date(value)
    if not d:
        return value or ""
    return d.strftime("%b %d, %Y")


def deduplicate_sales_rows(rows):
    """Remove duplicate sales rows while preserving the first occurrence."""
    seen = set()
    deduped = []
    for row in rows or []:
        normalized = normalize_record(row)
        signature = (
            normalized.get("date"),
            normalized.get("bread_type"),
            normalized.get("products_produced"),
            normalized.get("actual_qty_sold"),
            normalized.get("waste_returns"),
            normalized.get("price_per_product"),
            normalized.get("temperature"),
            normalized.get("is_holiday"),
            normalized.get("is_promotion"),
            normalized.get("sacks_used"),
            normalized.get("plates_used"),
            normalized.get("expense_amount"),
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(normalized)
    return deduped


def normalize_record(row):
    """Normalize incoming sales rows into a consistent internal record format."""
    raw_date = (
        row.get("date")
        or row.get("production_date")
        or row.get("sale_date")
        or row.get("baked_day")
    )
    parsed = parse_date(raw_date)
    record = {
        "date": parsed.isoformat() if parsed else "",
        "bread_type": row.get("bread_Type") or row.get("bread_type") or row.get("bread") or "Pandesal",
        "products_produced": int(float(row.get("Products_Produced") or row.get("products_produced") or row.get("produced") or 0)),
        "actual_qty_sold": int(float(row.get("Actual_Qty_Sold") or row.get("actual_qty_sold") or row.get("sales") or 0)),
        "waste_returns": int(float(row.get("Waste_Returns") or row.get("waste_returns") or row.get("waste") or 0)),
        "price_per_product": float(row.get("Price_Per_Product") or row.get("price_per_product") or row.get("price") or 0.0),
        "temperature": float(row.get("temperature") or 30.0),
        "is_holiday": int(float(row.get("is_holiday") or 0)),
        "is_promotion": int(float(row.get("is_promotion") or 0)),
        "sacks_used": int(float(row.get("Sacks_Used") or row.get("sacks_used") or 0)),
        "plates_used": int(float(row.get("Plates_Used") or row.get("plates_used") or 0)),
        "expense_amount": float(row.get("Expense_Amount") or row.get("expense_amount") or row.get("expense") or row.get("cost") or 0.0),
    }
    # Preserve numeric id when present (e.g., rows loaded from MySQL)
    id_val = row.get("id") or row.get("ID") or row.get("Id") or None
    try:
        id_val = int(id_val) if id_val not in (None, "") else None
    except Exception:
        id_val = None
    record["id"] = id_val
    return record


def compute_lag_features(record, history):
    """Compute lag features from previously seen sales history for the same bread type."""
    parsed_date = parse_date(record.get("date"))
    if not parsed_date:
        return {
            "prev_day_sales": 0,
            "prev_3day_avg_sales": 0,
            "prev_7day_avg_sales": 0,
            "prev_14day_avg_sales": 0,
            "prev_14day_sum_sales": 0,
            "prev_same_weekday_avg_sales": 0,
        }

    bread_type = record.get("bread_type", "Pandesal")
    bread_history = history.get(bread_type, {})

    def get_sales_for_day(day_delta):
        day = parsed_date - timedelta(days=day_delta)
        return int(bread_history.get(day.isoformat(), 0))

    prev_day_sales = get_sales_for_day(1)
    prev_3day_sales = [get_sales_for_day(i) for i in range(1, 4)]
    prev_7day_sales = [get_sales_for_day(i) for i in range(1, 8)]
    same_weekday_sales = [get_sales_for_day(7 * i) for i in range(1, 5)]

    prev_14day_sales = [get_sales_for_day(i) for i in range(1, 15)]
    prev_14day_avg = int(round(sum(prev_14day_sales) / len(prev_14day_sales))) if prev_14day_sales else 0
    prev_14day_sum = sum(prev_14day_sales)
    same_weekday_sales = [get_sales_for_day(7 * i) for i in range(1, 5)]
    same_weekday_avg = int(round(sum(same_weekday_sales) / len(same_weekday_sales))) if same_weekday_sales else 0

    return {
        "prev_day_sales": prev_day_sales,
        "prev_3day_avg_sales": int(round(sum(prev_3day_sales) / len(prev_3day_sales))) if prev_3day_sales else 0,
        "prev_7day_avg_sales": int(round(sum(prev_7day_sales) / len(prev_7day_sales))) if prev_7day_sales else 0,
        "prev_14day_avg_sales": prev_14day_avg,
        "prev_14day_sum_sales": prev_14day_sum,
        "prev_same_weekday_avg_sales": same_weekday_avg,
    }


def build_feature_vector(record):
    """Convert a sales record into numeric features for the ML model."""
    bread_type = record.get("bread_type", "Pandesal")
    features = [1 if bread_type == bread else 0 for bread in BREAD_TYPES]
    parsed_date = parse_date(record.get("date"))
    weekday = parsed_date.weekday() if parsed_date else 0
    month = parsed_date.month if parsed_date else 1
    day_of_month = parsed_date.day if parsed_date else 1
    week_of_year = parsed_date.isocalendar()[1] if parsed_date else 1
    day_of_year = parsed_date.timetuple().tm_yday if parsed_date else 1
    is_weekend = 1 if weekday >= 5 else 0
    date_ordinal = parsed_date.toordinal() if parsed_date else 0
    features.extend([
        record.get("price_per_product", 0.0),
        record.get("temperature", 30.0),
        record.get("is_holiday", 0),
        record.get("is_promotion", 0),
        record.get("sacks_used", 0),
        record.get("plates_used", 0),
        weekday,
        is_weekend,
        month,
        day_of_month,
        week_of_year,
        day_of_year,
        date_ordinal,
        record.get("prev_day_sales", 0),
        record.get("prev_3day_avg_sales", 0),
        record.get("prev_7day_avg_sales", 0),
        record.get("prev_14day_avg_sales", 0),
        record.get("prev_14day_sum_sales", 0),
        record.get("prev_same_weekday_avg_sales", 0),
    ])
    return features


def prepare_training_data(sales):
    sorted_sales = sorted(
        (record for record in sales if record.get("date")),
        key=lambda r: (parse_date(r["date"]) or date.min, r.get("bread_type", ""))
    )
    history = {}
    X = []
    y = []
    for record in sorted_sales:
        lags = compute_lag_features(record, history)
        record_with_lags = {**record, **lags}
        X.append(build_feature_vector(record_with_lags))
        y.append(record["actual_qty_sold"])
        bread_type = record_with_lags.get("bread_type", "Pandesal")
        history.setdefault(bread_type, {})[record_with_lags["date"]] = record_with_lags["actual_qty_sold"]
    return np.array(X), np.array(y)


def calculate_accuracy_score(actual, predicted):
    """Return a stable accuracy percentage using a robust median relative error."""
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    if actual_array.size == 0 or predicted_array.size != actual_array.size:
        return 0

    error_ratio = np.abs(actual_array - predicted_array) / np.maximum(np.abs(actual_array), 1.0)
    error_ratio = np.clip(error_ratio, 0.0, 1.0)
    robust_error = float(np.median(error_ratio)) if error_ratio.size else 0.0
    accuracy = 100 - (robust_error * 100)
    return int(round(max(0, min(100, accuracy))))


def build_rolling_baseline_features(record, history, bread_type):
    """Create a small rolling-history feature set for fallback heuristic predictions."""
    parsed_date = parse_date(record.get("date"))
    if not parsed_date:
        return {}

    bread_history = history.get(bread_type, {})
    recent_window = []
    for offset in range(1, 31):
        day = parsed_date - timedelta(days=offset)
        recent_window.append(int(bread_history.get(day.isoformat(), 0)))

    recent_avg = int(round(sum(recent_window) / len(recent_window))) if recent_window else 0
    recent_sum = sum(recent_window)
    if recent_window:
        recent_trend = recent_window[0] - recent_window[-1]
    else:
        recent_trend = 0

    return {
        "rolling_avg_30": recent_avg,
        "rolling_sum_30": recent_sum,
        "rolling_trend_30": recent_trend,
    }


def train_model(sales):
    """Train a regression model on historical sales data and save metrics."""
    global MODEL
    X, y = prepare_training_data(sales)
    if not X.size or not y.size:
        MODEL = None
        return {"r2": 0.0, "mae": 0.0, "accuracy": 0, "trainingCount": 0, "testCount": 0}

    # Use log1p transformation on the target to improve stability
    y_log = np.log1p(y.astype(np.float64))

    try:
        model = HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_depth=6,
            max_iter=400,
            random_state=42,
        )

        if len(y) >= 30:
            split_index = max(int(len(y) * 0.8), 1)
            X_train, X_test = X[:split_index], X[split_index:]
            y_train, y_test = y_log[:split_index], y_log[split_index:]
        else:
            X_train, X_test, y_train, y_test = X, X, y_log, y_log

        try:
            model.fit(X_train, y_train)
        except TypeError:
            model.fit(X_train, y_train)

        y_pred_log = model.predict(X_test)
        predictions = np.maximum(0.0, np.expm1(y_pred_log))
        y_test_actual = np.expm1(y_test) if len(y_test) > 0 else np.array([])

        mae = mean_absolute_error(y_test_actual, predictions) if len(y_test_actual) > 0 else 0.0
        r2 = r2_score(y_test_actual, predictions) if len(y_test_actual) > 1 else 0.0
        accuracy = calculate_accuracy_score(y_test_actual, predictions)

        MODEL = model
        return {
            "r2": r2,
            "mae": mae,
            "accuracy": accuracy,
            "trainingCount": len(y_train),
            "testCount": len(y_test),
        }
    except Exception as err:
        print(f"ML training error ({'XGBoost' if USE_XGBOOST else 'LinearRegression'}):", err)
        MODEL = None
        return {"r2": 0.0, "mae": 0.0, "accuracy": 0, "trainingCount": 0, "testCount": 0}


def parse_csv_upload(file_storage):
    """Parse an uploaded CSV or Excel file and normalize rows into sales records."""
    if not file_storage or file_storage.filename == "":
        return []

    filename = (file_storage.filename or "").lower()
    try:
        file_storage.stream.seek(0)

        if filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_storage.stream)
            rows = df.to_dict(orient="records")
        else:
            text_stream = io.TextIOWrapper(file_storage.stream, encoding="utf-8-sig", errors="replace")
            reader = csv.DictReader(text_stream)
            rows = [r for r in reader if r and any((v or "").strip() for v in r.values())]

        normalized = [normalize_record(r) for r in rows]

        # Aggregate by (date, bread_type): sum quantities and expenses, compute weighted price
        agg = {}
        for rec in normalized:
            key = (rec.get("date"), rec.get("bread_type"))
            qty = int(rec.get("actual_qty_sold") or 0)
            price = float(rec.get("price_per_product") or 0.0)
            produced = int(rec.get("products_produced") or 0)
            waste = int(rec.get("waste_returns") or 0)
            expense = float(rec.get("expense_amount") or 0.0)

            if key not in agg:
                a = dict(rec)
                a["products_produced"] = produced
                a["actual_qty_sold"] = qty
                a["waste_returns"] = waste
                a["expense_amount"] = expense
                a["_revenue"] = qty * price
                agg[key] = a
            else:
                a = agg[key]
                a["products_produced"] = int(a.get("products_produced", 0)) + produced
                a["actual_qty_sold"] = int(a.get("actual_qty_sold", 0)) + qty
                a["waste_returns"] = int(a.get("waste_returns", 0)) + waste
                a["expense_amount"] = float(a.get("expense_amount", 0.0)) + expense
                a["_revenue"] = float(a.get("_revenue", 0.0)) + (qty * price)

        records = []
        for (d, b), a in agg.items():
            total_sold = int(a.get("actual_qty_sold", 0))
            revenue = float(a.pop("_revenue", 0.0))
            if total_sold > 0:
                a["price_per_product"] = round(revenue / total_sold, 2)
            else:
                a["price_per_product"] = float(a.get("price_per_product") or 0.0)
            records.append(a)

        return records
    except Exception as err:
        print("CSV upload error:", err)
        return None


def get_db_connection():
    """Return a new connection to the MySQL database."""
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        autocommit=False,
    )


def load_custom_bread_types():
    """Load user-added bread types and their default prices."""
    if not USE_MYSQL:
        return {}

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT bread_type, default_price FROM bread_type_catalog ORDER BY bread_type")
        return {row[0]: float(row[1]) for row in cursor.fetchall()}
    except mysql.connector.Error as err:
        logger.error("Could not load custom bread types: %s", err)
        return {}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def persist_custom_bread_type(bread_type, default_price):
    """Save a new bread type and its default price."""
    if not USE_MYSQL:
        return False, "Persistent product storage requires MySQL."

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT IGNORE INTO bread_type_catalog (bread_type, default_price) VALUES (%s, %s)",
            (bread_type, default_price),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return False, "A product with that name already exists."
        conn.commit()
        return True, None
    except mysql.connector.Error as err:
        if conn:
            conn.rollback()
        logger.error("Could not save custom bread type: %s", err)
        return False, "Could not save this product. Please try again."
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def ensure_database():
    """Create the configured MySQL database if it does not yet exist."""
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        print("MySQL ensure_database error:", err)


def ensure_monthly_expenses_table_schema():
    """Ensure older MySQL databases have the scope columns needed by the expense forms."""
    if not USE_MYSQL:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM monthly_expenses")
        columns = {row[0].lower() for row in cursor.fetchall()}
        if "scope_type" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN scope_type ENUM('daily','weekly','monthly') NOT NULL DEFAULT 'monthly'")
        if "period_key" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN period_key VARCHAR(20) NOT NULL DEFAULT 'monthly'")
        if "expense_month" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN expense_month DATE NULL")
        if "note" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN note VARCHAR(255) NULL")
        if "created_at" not in columns:
            cursor.execute("ALTER TABLE monthly_expenses ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        try:
            cursor.execute("ALTER TABLE monthly_expenses ADD UNIQUE INDEX unique_scope_category (scope_type, period_key, category)")
        except mysql.connector.Error:
            pass
        conn.commit()
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        print("MySQL monthly expenses schema ensure error:", err)


def init_db():
    if not USE_MYSQL:
        return
    # Ensure the database exists first
    ensure_database()

    # First connection: core tables and repairs
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create sales_data table with unique constraint on (date, bread_type)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales_data (
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE NOT NULL,
                bread_type VARCHAR(100) NOT NULL,
                products_produced INT NOT NULL DEFAULT 0,
                actual_qty_sold INT NOT NULL DEFAULT 0,
                waste_returns INT NOT NULL DEFAULT 0,
                price_per_product DECIMAL(10,2) NOT NULL DEFAULT 0.0,
                temperature DECIMAL(5,2) NOT NULL DEFAULT 30.0,
                is_holiday TINYINT(1) NOT NULL DEFAULT 0,
                is_promotion TINYINT(1) NOT NULL DEFAULT 0,
                sacks_used INT NOT NULL DEFAULT 0,
                plates_used INT NOT NULL DEFAULT 0,
                expense_amount DECIMAL(10,2) NOT NULL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_date_bread_type (date, bread_type),
                INDEX idx_date (date),
                INDEX idx_bread_type (bread_type)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bread_type_catalog (
                id INT AUTO_INCREMENT PRIMARY KEY,
                bread_type VARCHAR(100) NOT NULL UNIQUE,
                default_price DECIMAL(10,2) NOT NULL DEFAULT 4.50,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)
        cursor.execute("SHOW COLUMNS FROM bread_type_catalog LIKE 'default_price'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE bread_type_catalog ADD COLUMN default_price DECIMAL(10,2) NOT NULL DEFAULT 4.50"
            )

        # Repair older imported schemas that may have been created without
        # AUTO_INCREMENT on the primary key. This is required for the app's
        # save path to work against a cloud database imported from the SQL dump.
        try:
            cursor.execute("SHOW INDEX FROM sales_data WHERE Key_name = 'PRIMARY'")
            primary_key_exists = cursor.fetchone() is not None
            if not primary_key_exists:
                cursor.execute("ALTER TABLE sales_data ADD PRIMARY KEY (id)")
        except mysql.connector.Error as err:
            logger.debug("sales_data primary key repair warning: %s", err)

        try:
            cursor.execute("ALTER TABLE sales_data MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT")
        except mysql.connector.Error as err:
            logger.debug("sales_data id repair warning: %s", err)

        for column_name, definition in (
            ("bad_waste", "INT NOT NULL DEFAULT 0"),
            ("good_waste", "INT NOT NULL DEFAULT 0"),
        ):
            try:
                cursor.execute(f"SHOW COLUMNS FROM sales_data LIKE '{column_name}'")
                if cursor.fetchone() is None:
                    cursor.execute(f"ALTER TABLE sales_data ADD COLUMN {column_name} {definition}")
            except mysql.connector.Error as err:
                logger.debug("sales_data column repair warning (%s): %s", column_name, err)

        # Add unique constraint to existing table if not already present
        try:
            cursor.execute("""
                ALTER TABLE sales_data ADD CONSTRAINT uk_date_bread_type UNIQUE (date, bread_type)
            """)
        except mysql.connector.Error as err:
            # Constraint already exists or other error, ignore unless unexpected
            if 'Duplicate key name' not in str(err):
                logger.debug("ALTER TABLE warning (may be OK): %s", err)

        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(50) DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP NULL,
                is_active TINYINT(1) DEFAULT 1,
                INDEX idx_username (username)
            ) ENGINE=InnoDB
        """)

        # Create model_performance table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_performance (
                id INT AUTO_INCREMENT PRIMARY KEY,
                training_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                r2_score DECIMAL(5,4) NOT NULL,
                mae DECIMAL(10,2) NOT NULL,
                accuracy INT NOT NULL,
                training_count INT NOT NULL,
                test_count INT NOT NULL,
                model_version INT DEFAULT 1,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_training_date (training_date)
            ) ENGINE=InnoDB
        """)

        # Create predictions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                bread_type VARCHAR(100) NOT NULL,
                prediction_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_predicted DATE NOT NULL,
                temperature DECIMAL(5,2),
                is_holiday TINYINT(1) DEFAULT 0,
                is_promotion TINYINT(1) DEFAULT 0,
                predicted_demand INT NOT NULL,
                buffer INT DEFAULT 0,
                recommended_production INT NOT NULL,
                confidence INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_bread_type (bread_type),
                INDEX idx_date_predicted (date_predicted)
            ) ENGINE=InnoDB
        """)

        # Create production_plan table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS production_plan (
                id INT AUTO_INCREMENT PRIMARY KEY,
                plan_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                bread_type VARCHAR(100) NOT NULL,
                day_date DATE NOT NULL,
                planned_quantity INT NOT NULL DEFAULT 0,
                actual_quantity INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_plan_date (plan_date),
                INDEX idx_bread_type (bread_type),
                INDEX idx_day_date (day_date),
                UNIQUE KEY unique_plan_entry (plan_date, bread_type, day_date)
            ) ENGINE=InnoDB
        """)
        try:
            cursor.execute("ALTER TABLE production_plan ADD COLUMN actual_quantity INT NOT NULL DEFAULT 0")
        except mysql.connector.Error:
            pass
        try:
            cursor.execute("ALTER TABLE production_plan ADD UNIQUE INDEX unique_plan_entry (plan_date, bread_type, day_date)")
        except mysql.connector.Error:
            pass

        # Create monthly expenses table if it does not exist yet
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_expenses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                scope_type ENUM('daily','weekly','monthly') NOT NULL DEFAULT 'monthly',
                period_key VARCHAR(20) NOT NULL,
                expense_month DATE NULL,
                category VARCHAR(100) NOT NULL,
                amount DECIMAL(12,2) NOT NULL DEFAULT 0.0,
                note VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_expense_scope (scope_type, period_key),
                INDEX idx_expense_month (expense_month),
                INDEX idx_category (category),
                UNIQUE KEY unique_scope_category (scope_type, period_key, category)
            ) ENGINE=InnoDB
        """)
        conn.commit()
    except mysql.connector.Error as err:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("MySQL init error (core tables): %s", err)
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    # Ensure monthly expenses schema (separate connection inside function)
    ensure_monthly_expenses_table_schema()

    # Second connection: ingredients, transactions, and raw uploads
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create ingredients inventory table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingredients (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                current_stock DECIMAL(14,4) NOT NULL DEFAULT 0,
                unit VARCHAR(50) NOT NULL DEFAULT '',
                reorder_threshold DECIMAL(14,4) NOT NULL DEFAULT 0,
                last_restock DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ingredient_name (name)
            ) ENGINE=InnoDB
        """)

        # Create ingredient transactions log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingredient_transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                ingredient_id INT NOT NULL,
                change_amount DECIMAL(14,4) NOT NULL,
                transaction_type ENUM('in','out') NOT NULL,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ingredient_txn (ingredient_id),
                CONSTRAINT fk_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        try:
            cursor.execute("SHOW COLUMNS FROM sales_data LIKE 'expense_amount'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE sales_data ADD COLUMN expense_amount DECIMAL(10,2) NOT NULL DEFAULT 0.0")
        except mysql.connector.Error as err:
            logger.debug("sales_data expense_amount repair warning: %s", err)

        # Create a raw uploads table to preserve original CSV uploads (no unique constraint)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_sales_uploads (
                id INT AUTO_INCREMENT PRIMARY KEY,
                date DATE,
                bread_type VARCHAR(200),
                products_produced INT DEFAULT 0,
                actual_qty_sold INT DEFAULT 0,
                waste_returns INT DEFAULT 0,
                price_per_product DECIMAL(10,2) DEFAULT 0.0,
                temperature DECIMAL(5,2) DEFAULT 30.0,
                is_holiday TINYINT(1) DEFAULT 0,
                is_promotion TINYINT(1) DEFAULT 0,
                sacks_used INT DEFAULT 0,
                plates_used INT DEFAULT 0,
                expense_amount DECIMAL(10,2) DEFAULT 0.0,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_raw_uploaded_at (uploaded_at)
            ) ENGINE=InnoDB
        """)
        conn.commit()
        logger.info("Database tables initialized successfully")
    except mysql.connector.Error as err:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("MySQL init error (ingredients/raw uploads): %s", err)
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def fetch_sales_from_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, date, bread_type, products_produced, actual_qty_sold,
                   waste_returns, price_per_product, temperature,
                   is_holiday, is_promotion, sacks_used, plates_used, expense_amount
            FROM sales_data
            ORDER BY date, bread_type
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [normalize_record(row) for row in rows]
    except mysql.connector.Error as err:
        print("MySQL fetch error:", err)
        return None


def parse_month(value):
    if not value:
        return None
    try:
        year, month = map(int, value.split("-"))
        return date(year, month, 1)
    except Exception:
        return None


def parse_week(value):
    if not value:
        return None
    try:
        year, week = value.split("-W")
        return int(year), int(week)
    except Exception:
        return None


def get_expense_scope_context(scope_type="monthly", selected_date=None, selected_week=None, selected_month=None):
    """Build a context object for the selected expense scope."""
    scope_type = (scope_type or "monthly").strip().lower()
    if scope_type == "daily":
        expense_date = parse_date(selected_date) or date.today()
        return {
            "scope_type": "daily",
            "period_key": expense_date.strftime("%Y-%m-%d"),
            "expense_date": expense_date,
            "expense_week": None,
            "expense_month": None,
        }
    if scope_type == "weekly":
        year, week = parse_week(selected_week) or (date.today().isocalendar().year, date.today().isocalendar().week)
        return {
            "scope_type": "weekly",
            "period_key": f"{year}-W{week}",
            "expense_date": None,
            "expense_week": week,
            "expense_month": None,
        }
    expense_month = parse_month(selected_month) or date.today().replace(day=1)
    return {
        "scope_type": "monthly",
        "period_key": expense_month.strftime("%Y-%m"),
        "expense_date": None,
        "expense_week": None,
        "expense_month": expense_month,
    }


def save_monthly_expense(month_value, category, amount, note="", scope_type="monthly", period_key=None):
    ensure_monthly_expenses_table_schema()
    scope_type = (scope_type or "monthly").strip().lower()
    if scope_type == "daily":
        print("month_value:", repr(month_value))
        print("period_key:", repr(period_key))

        expense_date = parse_date(period_key or month_value)

        print("parsed expense_date:", repr(expense_date))

        if expense_date is None:
            expense_date = date.today()

        print("final expense_date:", repr(expense_date))

        period_value = expense_date.strftime("%Y-%m-%d")
        expense_month_value = expense_date

        print("expense_month_value:", repr(expense_month_value))
    elif scope_type == "weekly":
        week_value = period_key or month_value
        if isinstance(week_value, str) and "-W" in week_value:
            period_value = week_value
        else:
            period_value = f"{date.today().year}-W{date.today().isocalendar().week:02d}"
        # For weekly, set expense_month_value to the ISO week start (Monday)
        try:
            y, w = parse_week(week_value) or (date.today().isocalendar().year, date.today().isocalendar().week)
            expense_month_value = date.fromisocalendar(int(y), int(w), 1)
        except Exception:
            expense_month_value = None
    else:
        expense_month_value = parse_month(period_key or month_value) or date.today().replace(day=1)
        period_value = expense_month_value.strftime("%Y-%m")

    if not USE_MYSQL:
        bucket = MONTHLY_EXPENSES.setdefault((scope_type, period_value), {})
        bucket[category] = {"amount": float(amount), "note": note or ""}
        return True

    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            # Diagnostic: print current database for hosted checks
            try:
                cursor.execute("SELECT DATABASE()")
                print("DB:", cursor.fetchone())
                # Also fetch and print the CREATE TABLE output for diagnostics on Render
                try:
                    cursor.execute("SHOW CREATE TABLE monthly_expenses")
                    table = cursor.fetchone()
                    print("TABLE FROM RENDER:")
                    print(table)
                except Exception:
                    # If SHOW CREATE TABLE fails, continue without blocking saves
                    pass
            except Exception:
                pass

            params = (scope_type, period_value, expense_month_value, category, float(amount), note)
            print("Saving monthly expense, params:", repr(params))
            cursor.execute(
                """
                INSERT INTO monthly_expenses (scope_type, period_key, expense_month, category, amount, note)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE amount = VALUES(amount), note = VALUES(note)
                """,
                params,
            )
            print("Rows affected:", getattr(cursor, 'rowcount', None))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except mysql.connector.Error as err:
            # Return error message so caller can show it
            print("MySQL monthly expense save error:", err)
            return False


def fetch_monthly_expenses(month_value, scope_type="monthly", period_key=None):
    ensure_monthly_expenses_table_schema()
    scope_type = (scope_type or "monthly").strip().lower()
    if scope_type == "daily":
        expense_date = parse_date(period_key or month_value) or date.today()
        period_value = expense_date.strftime("%Y-%m-%d")
    elif scope_type == "weekly":
        period_value = period_key or month_value
    else:
        expense_month_value = parse_month(period_key or month_value) or date.today().replace(day=1)
        period_value = expense_month_value.strftime("%Y-%m")

    if not USE_MYSQL:
        bucket = MONTHLY_EXPENSES.get((scope_type, period_value), {})
        return {
            category: {"amount": float(entry.get("amount", 0.0)), "note": entry.get("note", "")}
            for category, entry in bucket.items()
        }

    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT category, amount, note FROM monthly_expenses WHERE scope_type = %s AND period_key = %s",
                (scope_type, period_value)
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return {row['category']: {'amount': float(row['amount']), 'note': row['note'] or ''} for row in rows}
        except mysql.connector.Error as err:
            print("MySQL monthly expense fetch error:", err)
    return {}


def fetch_monthly_expense_total(month_value, scope_type="monthly", period_key=None):
    expenses = fetch_monthly_expenses(month_value, scope_type=scope_type, period_key=period_key)
    return sum(item['amount'] for item in expenses.values())


def build_expense_history_rows(entries, limit=None):
    """Return expense rows sorted newest-first for the history UI and tests."""
    normalized = []
    for entry in entries or []:
        created_at_raw = entry.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00")) if created_at_raw else datetime.min
        except ValueError:
            created_at = datetime.min

        normalized.append({
            "scope_type": entry.get("scope_type", "monthly"),
            "period_key": entry.get("period_key", ""),
            "category": entry.get("category", ""),
            "amount": float(entry.get("amount", 0.0)),
            "note": entry.get("note", ""),
            "created_at": created_at,
        })

    normalized.sort(key=lambda item: item["created_at"], reverse=True)
    if limit is not None:
        normalized = normalized[:limit]

    return [
        {
            "scope_type": item["scope_type"],
            "period_key": item["period_key"],
            "category": item["category"],
            "amount": item["amount"],
            "note": item["note"],
            "created_at": item["created_at"].isoformat(),
        }
        for item in normalized
    ]


def fetch_all_expenses_total():
    """Fetch total expenses for all months."""
    if not USE_MYSQL:
        return sum(
            float(entry.get("amount", 0.0))
            for bucket in MONTHLY_EXPENSES.values()
            for entry in bucket.values()
        )

    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(amount) FROM monthly_expenses")
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            if result and result[0] is not None:
                return float(result[0])
            return 0.0
        except mysql.connector.Error as err:
            print("MySQL fetch all expenses error:", err)
            return 0.0
    else:
        # For in-memory, we don't have monthly expenses stored
        return 0.0


def get_future_demand_forecast(sales_data, days_ahead=30):
    """Generate future demand forecast for the next N days."""
    from datetime import date, timedelta
    
    today = date.today()
    forecast_data = []
    
    for i in range(days_ahead):
        target_date = today + timedelta(days=i+1)  # Start from tomorrow
        
        # Get day of week and determine if it's a weekend
        is_weekend = target_date.weekday() >= 5
        
        # Estimate temperature (rough seasonal approximation)
        # This is a simple approximation - could be improved with actual weather data
        month = target_date.month
        if month in [12, 1, 2]:  # Winter
            base_temp = 25
        elif month in [3, 4, 5]:  # Spring
            base_temp = 28
        elif month in [6, 7, 8]:  # Summer
            base_temp = 32
        else:  # Fall
            base_temp = 30
        
        temperature = base_temp + random.randint(-3, 3)
        
        # Check if it's a holiday (simplified - you might want to add actual holiday calendar)
        is_holiday = False  # Could be enhanced with holiday API
        
        # Predict demand for all bread types combined
        results = predict_demand("All", target_date, temperature, is_holiday, False, sales_data)
        total_predicted = sum(item["predictedDemand"] for item in results)
        
        forecast_data.append({
            "date": target_date.strftime("%Y-%m-%d"),
            "date_label": target_date.strftime("%b %d"),
            "predicted_demand": total_predicted,
            "temperature": temperature,
            "is_weekend": is_weekend,
            "is_holiday": is_holiday
        })
    
    return forecast_data


def get_monthly_analytics(sales_data, months=12):
    """Generate monthly analytics for sales, expenses, and profit."""
    from collections import defaultdict
    import calendar
    
    # Get current date
    today = date.today()
    
    # Start from January 2026
    start_year = 2026
    start_month = 1
    current_date = today.replace(day=1)
    
    # Calculate number of months from Jan 2026 to current month
    current_year = today.year
    current_month = today.month
    months_diff = (current_year - start_year) * 12 + (current_month - start_month)
    actual_months = min(months_diff + 1, months)  # Include current month
    
    # Initialize monthly data
    monthly_data = []
    
    for i in range(actual_months):
        # Calculate target year and month by properly incrementing months
        target_month = start_month + i
        target_year = start_year + (target_month - 1) // 12
        target_month = ((target_month - 1) % 12) + 1
        
        target_date = date(target_year, target_month, 1)
        
        # Don't go beyond current month
        if target_date > current_date:
            break
            
        month_key = target_date.strftime("%Y-%m")
        month_name = target_date.strftime("%b %Y")
        
        # Filter sales for this month
        month_sales = [row for row in sales_data if row["date"].startswith(month_key)]
        
        # Calculate sales and revenue for this month
        monthly_sales = sum(row["actual_qty_sold"] for row in month_sales)
        monthly_revenue = sum(row["actual_qty_sold"] * row["price_per_product"] for row in month_sales)
        
        # Get expenses for this month
        monthly_expenses = fetch_monthly_expense_total(month_key)
        
        # Calculate profit
        monthly_profit = monthly_revenue - monthly_expenses
        
        monthly_data.append({
            "month": month_name,
            "month_key": month_key,
            "sales": monthly_sales,
            "revenue": monthly_revenue,
            "expenses": monthly_expenses,
            "profit": monthly_profit
        })
    
    return monthly_data


def build_batch_entry_records(start_date, scope_days, bread_inputs):
    """Expand a batch form submission into one sales record per bread/date combination."""
    start = parse_date(start_date) or date.today()
    scope_days = max(1, min(int(scope_days or 1), 7))
    records = []

    for day_offset in range(scope_days):
        target_date = start + timedelta(days=day_offset)
        for entry in bread_inputs or []:
            bread_type = entry.get("bread_type") or "Pandesal"
            produced = int(entry.get("produced") or 0)
            sold = int(entry.get("sold") or 0)
            price = float(entry.get("price") or 4.5)
            records.append({
                "date": target_date.isoformat(),
                "bread_type": bread_type,
                "products_produced": produced,
                "actual_qty_sold": sold,
                "waste_returns": max(0, produced - sold),
                "price_per_product": price,
                "temperature": 30.0,
                "is_holiday": 0,
                "is_promotion": 0,
                "sacks_used": 0,
                "plates_used": 0,
                "expense_amount": 0.0,
            })

    return records


def save_sales_record(record):
    """Save or update a sales record using UPSERT logic to prevent duplicates."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sales_data (
                    date, bread_type, products_produced, actual_qty_sold,
                    waste_returns, price_per_product, temperature,
                    is_holiday, is_promotion, sacks_used, plates_used, expense_amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    products_produced = VALUES(products_produced),
                    actual_qty_sold = VALUES(actual_qty_sold),
                    waste_returns = VALUES(waste_returns),
                    price_per_product = VALUES(price_per_product),
                    temperature = VALUES(temperature),
                    is_holiday = VALUES(is_holiday),
                    is_promotion = VALUES(is_promotion),
                    sacks_used = VALUES(sacks_used),
                    plates_used = VALUES(plates_used),
                    expense_amount = VALUES(expense_amount)
            """, (
                record["date"],
                record["bread_type"],
                record["products_produced"],
                record["actual_qty_sold"],
                record["waste_returns"],
                record["price_per_product"],
                record["temperature"],
                record["is_holiday"],
                record["is_promotion"],
                record["sacks_used"],
                record["plates_used"],
                record.get("expense_amount", 0.0),
            ))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except mysql.connector.Error as err:
            print("MySQL upsert error:", err)
            return False
    return False


def reduce_ingredients_for_sale(bread_type, quantity_sold):
    """Automatically deduct ingredients from inventory when a sale is logged.
    
    Args:
        bread_type: The type of bread sold (e.g., 'Pandesal')
        quantity_sold: The quantity of units sold
    
    Returns:
        Dictionary with deduction status and any warnings:
        {'success': bool, 'warnings': [list of low-stock warnings]}
    """
    result = {'success': True, 'warnings': []}
    
    # Get recipe for this bread type
    if bread_type not in BREAD_RECIPES:
        # Default recipe for unmapped bread types (use minimal ingredients)
        result['warnings'].append(f"No recipe found for {bread_type}. Skipping ingredient deduction.")
        return result
    
    recipe = BREAD_RECIPES[bread_type]
    
    if not USE_MYSQL:
        return result  # Skip in non-MySQL mode
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        for ingredient_name, quantity_per_unit in recipe.items():
            total_deduction = quantity_per_unit * quantity_sold
            
            # Get current ingredient stock
            cursor.execute(
                "SELECT id, current_stock, reorder_threshold, unit FROM ingredients WHERE name = %s",
                (ingredient_name,)
            )
            ingredient = cursor.fetchone()
            
            if not ingredient:
                result['warnings'].append(f"Ingredient '{ingredient_name}' not found in database.")
                continue
            
            ingredient_id = ingredient['id']
            current_stock = float(ingredient['current_stock'])
            new_stock = current_stock - total_deduction
            reorder_threshold = float(ingredient['reorder_threshold'])
            unit = ingredient['unit']
            
            # Update ingredient stock
            cursor.execute(
                "UPDATE ingredients SET current_stock = %s WHERE id = %s",
                (new_stock, ingredient_id)
            )
            
            # Log the transaction
            cursor.execute(
                """INSERT INTO ingredient_transactions 
                   (ingredient_id, change_amount, transaction_type, note, created_at)
                   VALUES (%s, %s, %s, %s, NOW())""",
                (ingredient_id, -total_deduction, 'out', 
                 f'Auto-deduction: {quantity_sold} units of {bread_type} sold')
            )
            
            # Check if stock fell below reorder threshold
            if new_stock < reorder_threshold:
                result['warnings'].append(
                    f"⚠️ {ingredient_name} ({unit}): Stock at {new_stock:.2f} - Below reorder threshold of {reorder_threshold:.2f}"
                )
        
        conn.commit()
        cursor.close()
        conn.close()
        
    except mysql.connector.Error as err:
        print(f"Error reducing ingredients for {bread_type}: {err}")
        result['success'] = False
        result['warnings'].append(f"Database error during ingredient deduction: {str(err)}")
    
    return result


def save_raw_upload_row(record):
    """Save the original uploaded row into raw_sales_uploads for recovery."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO raw_sales_uploads (
                    date, bread_type, products_produced, actual_qty_sold,
                    waste_returns, price_per_product, temperature,
                    is_holiday, is_promotion, sacks_used, plates_used, expense_amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                record.get("date"),
                record.get("bread_type"),
                record.get("products_produced", 0),
                record.get("actual_qty_sold", 0),
                record.get("waste_returns", 0),
                record.get("price_per_product", 0.0),
                record.get("temperature", 30.0),
                record.get("is_holiday", 0),
                record.get("is_promotion", 0),
                record.get("sacks_used", 0),
                record.get("plates_used", 0),
                record.get("expense_amount", 0.0),
            ))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL raw upload save error:", err)


def fetch_raw_sales_uploads():
    """Fetch all raw uploaded sales rows from the database."""
    if not USE_MYSQL:
        return []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, date, bread_type, products_produced, actual_qty_sold,
                   waste_returns, price_per_product, temperature,
                   is_holiday, is_promotion, sacks_used, plates_used, expense_amount,
                   uploaded_at
            FROM raw_sales_uploads
            ORDER BY uploaded_at, id
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [normalize_record(row) for row in rows]
    except mysql.connector.Error as err:
        print("MySQL raw uploads fetch error:", err)
        return []


def restore_sales_data_from_raw_uploads():
    """Restore the sales_data table from raw uploaded records.

    This rebuilds the aggregated sales_data set from preserved raw uploads.
    """
    if not USE_MYSQL:
        return 0

    raw_rows = fetch_raw_sales_uploads()
    if not raw_rows:
        return 0

    agg = {}
    for rec in raw_rows:
        key = (rec.get("date"), rec.get("bread_type"))
        qty = int(rec.get("actual_qty_sold") or 0)
        price = float(rec.get("price_per_product") or 0.0)
        produced = int(rec.get("products_produced") or 0)
        waste = int(rec.get("waste_returns") or 0)
        expense = float(rec.get("expense_amount") or 0.0)

        if key not in agg:
            a = dict(rec)
            a["products_produced"] = produced
            a["actual_qty_sold"] = qty
            a["waste_returns"] = waste
            a["expense_amount"] = expense
            a["_revenue"] = qty * price
            agg[key] = a
        else:
            a = agg[key]
            a["products_produced"] = int(a.get("products_produced", 0)) + produced
            a["actual_qty_sold"] = int(a.get("actual_qty_sold", 0)) + qty
            a["waste_returns"] = int(a.get("waste_returns", 0)) + waste
            a["expense_amount"] = float(a.get("expense_amount", 0.0)) + expense
            a["_revenue"] = float(a.get("_revenue", 0.0)) + (qty * price)

    for a in agg.values():
        total_sold = int(a.get("actual_qty_sold") or 0)
        revenue = float(a.pop("_revenue", 0.0))
        if total_sold > 0:
            a["price_per_product"] = round(revenue / total_sold, 2)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE sales_data")
        conn.commit()
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        print("MySQL sales_data truncate error:", err)
        return 0

    for record in agg.values():
        save_sales_record(record)

    global SALES_DATA, WEEKLY_PLAN
    SALES_DATA = load_sales_data()
    WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
    retrain_model_async(SALES_DATA)

    return len(agg)


def cleanup_duplicate_sales():
    """Remove duplicate sales entries, keeping the first occurrence of each (date, bread_type) pair."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            # Delete all duplicates except the first (lowest id) for each (date, bread_type)
            cursor.execute("""
                DELETE FROM sales_data
                WHERE id NOT IN (
                    SELECT MIN(id) FROM (
                        SELECT MIN(id) as id FROM sales_data GROUP BY date, bread_type
                    ) AS keep_ids
                )
            """)
            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            print(f"[DEBUG] Cleaned up {deleted_count} duplicate sales records")
            return deleted_count
        except mysql.connector.Error as err:
            print("MySQL cleanup duplicates error:", err)
            return 0
    return 0


def clear_sales_data():
    global SALES_DATA
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("TRUNCATE TABLE sales_data")
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL clear error:", err)
    SALES_DATA = []


def generate_sample_sales_records():
    """Return a list of sample sales records for testing and demo purposes."""
    today = date.today()
    sample_dates = [today - timedelta(days=i) for i in range(0, 7)]
    sample_breads = [
        "Pandesal",
        "Ube Pandesal",
        "Cheese Bread",
        "Ensaymada",
        "Mamon",
        "Loose Bread",
    ]
    records = []
    for index, sample_date in enumerate(sample_dates):
        bread_type = sample_breads[index % len(sample_breads)]
        produced = 50 + (index * 5)
        sold = produced - (index % 4) * 3
        price = 4.50 + (index % 3) * 0.25
        waste = max(0, produced - sold)
        records.append({
            "date": sample_date.isoformat(),
            "bread_type": bread_type,
            "products_produced": produced,
            "actual_qty_sold": sold,
            "waste_returns": waste,
            "price_per_product": round(price, 2),
            "temperature": 28.0 + (index % 2),
            "is_holiday": 0,
            "is_promotion": 0,
            "sacks_used": 2 + (index % 2),
            "plates_used": 1 + (index % 3),
            "expense_amount": round(100.0 + index * 15.5, 2),
        })
    return records


def delete_sales_record(record_id):
    """Delete a specific sales record by ID."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sales_data WHERE id = %s", (record_id,))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL delete error:", err)


def load_sales_data():
    if USE_MYSQL:
        rows = fetch_sales_from_db()
        if rows is not None:
            if not rows:
                restored = restore_sales_data_from_raw_uploads()
                if restored > 0:
                    rows = fetch_sales_from_db() or []
            return deduplicate_sales_rows(rows)
        print("Falling back to JSON data because MySQL is unavailable.")
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, encoding="utf-8") as file:
        raw = json.load(file)
    return deduplicate_sales_rows(raw)


def save_model_performance(metrics):
    """Save model performance metrics to database"""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO model_performance (
                    r2_score, mae, accuracy, training_count, test_count
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                metrics.get("r2", 0),
                metrics.get("mae", 0),
                metrics.get("accuracy", 0),
                metrics.get("trainingCount", 0),
                metrics.get("testCount", 0),
            ))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL model_performance save error:", err)


def fetch_latest_model_performance():
    """Fetch latest model performance from database"""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT r2_score, mae, accuracy, training_count, test_count
                FROM model_performance
                ORDER BY created_at DESC LIMIT 1
            """)
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                return {
                    "r2": float(row["r2_score"]),
                    "mae": float(row["mae"]),
                    "accuracy": int(row["accuracy"]),
                    "trainingCount": int(row["training_count"]),
                    "testCount": int(row["test_count"]),
                }
        except mysql.connector.Error as err:
            print("MySQL model_performance fetch error:", err)
    return None


def save_predictions(results_list, target_date):
    """Save predictions to database."""
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            for result in results_list:
                cursor.execute("""
                    INSERT INTO predictions (
                        bread_type, date_predicted, predicted_demand, buffer,
                        recommended_production, confidence
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    result["breadType"],
                    target_date,
                    result["predictedDemand"],
                    result["buffer"],
                    result["recommendedProduction"],
                    result["confidence"],
                ))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL predictions save error:", err)


def build_production_log_rows(prediction_rows, sales_rows):
    """Build rows for the production log from past predictions and matching sales outcomes."""
    rows = []
    sales_index = {}
    for sale in sales_rows or []:
        date_key = str(sale.get("date") or "")
        bread_type = sale.get("bread_type") or ""
        if not date_key or not bread_type:
            continue
        sales_index[(date_key, bread_type)] = {
            "actual_production": int(sale.get("products_produced") or 0),
            "actual_sales": int(sale.get("actual_qty_sold") or 0),
            "waste": int(sale.get("waste_returns") or 0),
        }

    for item in prediction_rows or []:
        date_predicted = item.get("date_predicted")
        date_key = date_predicted.isoformat() if isinstance(date_predicted, date) else str(date_predicted or "")
        bread_type = item.get("bread_type") or ""
        sales_outcome = sales_index.get((date_key, bread_type), {})
        rows.append({
            "bread_type": bread_type,
            "date": date_key,
            "predicted_demand": int(item.get("predicted_demand") or 0),
            "recommended_production": int(item.get("recommended_production") or 0),
            "actual_production": int(sales_outcome.get("actual_production") or 0),
            "confidence": int(item.get("confidence") or 0),
            "waste": int(sales_outcome.get("waste") or 0),
        })

    return rows


def save_production_outcome(date_value, bread_type, field, value):
    """Update one production outcome without changing other sales values."""
    column_by_field = {
        "actual_production": "products_produced",
        "waste": "waste_returns",
    }
    column = column_by_field.get(field)
    target_date = parse_date(date_value)
    if not column or not target_date or not bread_type:
        return False, "Invalid production record."

    if USE_MYSQL:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            produced = value if field == "actual_production" else 0
            waste = value if field == "waste" else 0
            cursor.execute(f"""
                INSERT INTO sales_data (
                    date, bread_type, products_produced, actual_qty_sold,
                    waste_returns, price_per_product, temperature, is_holiday,
                    is_promotion, sacks_used, plates_used, expense_amount
                ) VALUES (%s, %s, %s, 0, %s, 0, 30, 0, 0, 0, 0, 0)
                ON DUPLICATE KEY UPDATE {column} = VALUES({column})
            """, (target_date.isoformat(), bread_type, produced, waste))
            conn.commit()
        except mysql.connector.Error as err:
            if conn:
                conn.rollback()
            logger.error("Could not update production outcome: %s", err)
            return False, "Could not save this value. Please try again."
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    sale = next((
        item for item in SALES_DATA
        if str(item.get("date") or "") == target_date.isoformat() and item.get("bread_type") == bread_type
    ), None)
    if sale is None:
        sale = {
            "date": target_date.isoformat(),
            "bread_type": bread_type,
            "products_produced": 0,
            "actual_qty_sold": 0,
            "waste_returns": 0,
            "price_per_product": 0.0,
            "temperature": 30.0,
            "is_holiday": 0,
            "is_promotion": 0,
            "sacks_used": 0,
            "plates_used": 0,
            "expense_amount": 0.0,
        }
        SALES_DATA.append(sale)
    sale["products_produced" if field == "actual_production" else "waste_returns"] = value

    return True, None


def fetch_prediction_records(filter_date=None):
    """Return saved prediction records from the database."""
    if not USE_MYSQL:
        return []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT bread_type, date_predicted, predicted_demand, buffer,
                   recommended_production, confidence, created_at
            FROM predictions
        """
        params = ()
        if filter_date:
            query += "\n            WHERE date_predicted = %s"
            params = (filter_date,)
        query += "\n            ORDER BY created_at DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                "bread_type": row["bread_type"],
                "date_predicted": row["date_predicted"].isoformat() if row["date_predicted"] else "",
                "predicted_demand": int(row["predicted_demand"]),
                "buffer": int(row["buffer"]),
                "recommended_production": int(row["recommended_production"]),
                "confidence": int(row["confidence"]),
                "created_at": row["created_at"].isoformat() if row["created_at"] else "",
            }
            for row in rows
        ]
    except mysql.connector.Error as err:
        print("MySQL prediction records fetch error:", err)
        return []


def save_production_plan(weekly_plan, actual_quantities=None, plan_date=None):
    """Save production plan and optional actual outcomes to the database."""
    if USE_MYSQL and weekly_plan:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            plan_timestamp = plan_date or datetime.now()
            for day in weekly_plan:
                day_date = day.get("day_date") or day.get("date")
                for bread in BREAD_TYPES:
                    qty = day.get(bread, 0)
                    if not qty:
                        continue
                    actual_qty = 0
                    if actual_quantities:
                        key = f"{day_date}:{bread}"
                        try:
                            actual_qty = int(actual_quantities.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            actual_qty = 0
                    cursor.execute("""
                        INSERT INTO production_plan (
                            plan_date, bread_type, day_date, planned_quantity, actual_quantity
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            planned_quantity = VALUES(planned_quantity),
                            actual_quantity = VALUES(actual_quantity)
                    """, (plan_timestamp, bread, day_date, qty, actual_qty))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print("MySQL production_plan save error:", err)


def parse_datetime(value):
    """Convert a string or datetime-like value to a datetime.datetime object."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        pass
    return None


def format_plan_session_label(plan_dt):
    return plan_dt.strftime("%b %d, %Y %I:%M %p") if plan_dt else ""


def fetch_production_plan_sessions():
    """Return distinct saved production plan sessions from the database."""
    if not USE_MYSQL:
        return []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT DISTINCT plan_date FROM production_plan ORDER BY plan_date DESC")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                "value": row["plan_date"].strftime("%Y-%m-%d %H:%M:%S"),
                "label": format_plan_session_label(row["plan_date"]),
            }
            for row in rows
            if row.get("plan_date")
        ]
    except mysql.connector.Error as err:
        print("MySQL production_plan sessions error:", err)
        return []


def fetch_production_plan_rows(plan_date_value=None):
    """Return saved production plan rows for a specific plan session."""
    if not USE_MYSQL:
        return []
    target_plan_date = parse_datetime(plan_date_value) if plan_date_value else None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT bread_type, day_date, planned_quantity, actual_quantity
            FROM production_plan
        """
        params = ()
        if target_plan_date:
            query += "\n            WHERE plan_date = %s"
            params = (target_plan_date.strftime("%Y-%m-%d %H:%M:%S"),)
        query += "\n            ORDER BY day_date, bread_type"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                "bread_type": row["bread_type"],
                "day_date": row["day_date"],
                "planned_quantity": int(row["planned_quantity"]),
                "actual_quantity": int(row.get("actual_quantity") or 0),
            }
            for row in rows
        ]


    
    except mysql.connector.Error as err:
        print("MySQL production_plan fetch error:", err)
        return []


def build_production_history_grid(plan_rows):
    if not plan_rows:
        return {"columns": [], "rows": []}

    normalized_rows = []
    for row in plan_rows:
        day_date = row.get("day_date")
        parsed_day = parse_date(day_date) if day_date else None
        if not parsed_day or not row.get("bread_type"):
            continue
        normalized_rows.append({
            **row,
            "day_date": parsed_day,
            "planned_quantity": int(row.get("planned_quantity") or 0),
            "actual_quantity": int(row.get("actual_quantity") or 0),
        })

    if not normalized_rows:
        return {"columns": [], "rows": []}

    day_dates = sorted({row["day_date"] for row in normalized_rows})
    columns = [
        {
            "key": day_date.isoformat(),
            "day_name": day_date.strftime("%a").upper(),
            "day_label": day_date.strftime("%b %d").upper(),
        }
        for day_date in day_dates
    ]

    bread_types_in_plan = [
        bread for bread in BREAD_TYPES
        if any(row["bread_type"] == bread for row in normalized_rows)
    ]
    if not bread_types_in_plan:
        bread_types_in_plan = sorted({row["bread_type"] for row in normalized_rows})

    rows = []
    for bread in bread_types_in_plan:
        cells = []
        for col in columns:
            planned_quantity = 0
            actual_quantity = 0
            for plan in normalized_rows:
                if plan["bread_type"] == bread and plan["day_date"].isoformat() == col["key"]:
                    planned_quantity = int(plan.get("planned_quantity") or 0)
                    actual_quantity = int(plan.get("actual_quantity") or 0)
                    break
            cells.append({
                "day_key": col["key"],
                "planned": planned_quantity,
                "actual": actual_quantity,
            })
        rows.append({"bread_type": bread, "cells": cells})

    return {"columns": columns, "rows": rows}


def get_chart_data(sales_data):
    """Generate chart data for dashboard"""
    # Sales and production trend for last 30 days
    today = date.today()
    sales_trend_labels = []
    sales_trend_produced = []
    sales_trend_sold = []
    
    for i in range(29, -1, -1):
        target_date = today - timedelta(days=i)
        date_str = target_date.isoformat()
        daily_produced = sum(row["products_produced"] for row in sales_data if row["date"] == date_str)
        daily_sold = sum(row["actual_qty_sold"] for row in sales_data if row["date"] == date_str)
        sales_trend_labels.append(target_date.strftime("%b %d"))
        sales_trend_produced.append(daily_produced)
        sales_trend_sold.append(daily_sold)
    
    # Bread distribution data
    bread_totals = {}
    for row in sales_data:
        bread_totals[row["bread_type"]] = bread_totals.get(row["bread_type"], 0) + row["actual_qty_sold"]
    
    # Sort by sales volume (descending) to show all breads sorted by popularity
    sorted_breads = sorted(bread_totals.items(), key=lambda x: x[1], reverse=True)
    bread_labels = [label for label, value in sorted_breads]  # All bread types
    bread_data = [value for label, value in sorted_breads]
    
    # Weekly production vs sales data
    weekly_labels = []
    produced_data = []
    sold_data = []
    
    for i in range(6, -1, -1):
        target_date = today - timedelta(days=i)
        date_str = target_date.isoformat()
        weekly_labels.append(target_date.strftime("%a"))
        
        daily_produced = sum(row["products_produced"] for row in sales_data if row["date"] == date_str)
        daily_sold = sum(row["actual_qty_sold"] for row in sales_data if row["date"] == date_str)
        
        produced_data.append(daily_produced)
        sold_data.append(daily_sold)
    
    return {
        "sales_trend_labels": sales_trend_labels,
        "sales_trend_produced": sales_trend_produced,
        "sales_trend_sold": sales_trend_sold,
        "bread_labels": bread_labels,
        "bread_data": bread_data,
        "weekly_labels": weekly_labels,
        "produced_data": produced_data,
        "sold_data": sold_data,
    }


def get_dashboard_period_label(view_scope, view_date=None, view_week=None, view_month=None):
    """Return a display label for the selected dashboard scope and period."""
    if view_scope == 'daily' and view_date:
        d = parse_date(view_date)
        if d:
            return d.strftime("%b %d, %Y")
    elif view_scope == 'weekly' and view_week:
        try:
            # view_week format: YYYY-Www (e.g., 2026-W28)
            parts = view_week.split('-W')
            if len(parts) == 2:
                year = int(parts[0])
                week = int(parts[1])
                # Get Monday of that week
                jan4 = date(year, 1, 4)
                week_one_monday = jan4 - timedelta(days=jan4.weekday())
                monday = week_one_monday + timedelta(weeks=week - 1)
                sunday = monday + timedelta(days=6)
                return f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"
        except Exception:
            pass
    elif view_scope == 'monthly' and view_month:
        try:
            # view_month format: YYYY-MM
            d = datetime.strptime(view_month, "%Y-%m").date()
            return d.strftime("%B %Y")
        except Exception:
            pass
    elif view_scope == 'all_data':
        return "All Data (Lifetime)"
    
    return "Current Period"


def filter_sales_by_scope(sales, view_scope, view_date=None, view_week=None, view_month=None):
    """Filter sales data by the selected scope and period."""
    if view_scope == 'daily' and view_date:
        d = parse_date(view_date)
        if d:
            target_date = d.isoformat()
            return [row for row in sales if row.get("date") == target_date]
    elif view_scope == 'weekly' and view_week:
        try:
            parts = view_week.split('-W')
            if len(parts) == 2:
                year = int(parts[0])
                week = int(parts[1])
                jan4 = date(year, 1, 4)
                week_one_monday = jan4 - timedelta(days=jan4.weekday())
                monday = week_one_monday + timedelta(weeks=week - 1)
                sunday = monday + timedelta(days=6)
                return [row for row in sales if monday.isoformat() <= row.get("date", "") <= sunday.isoformat()]
        except Exception:
            pass
    elif view_scope == 'monthly' and view_month:
        try:
            d = datetime.strptime(view_month, "%Y-%m").date()
            month_start = d.replace(day=1).isoformat()
            if d.month == 12:
                month_end = d.replace(year=d.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = d.replace(month=d.month + 1, day=1) - timedelta(days=1)
            month_end = month_end.isoformat()
            return [row for row in sales if month_start <= row.get("date", "") <= month_end]
        except Exception:
            pass
    elif view_scope == 'all_data':
        return sales
    
    # Default: return all sales
    return sales


def compute_kpis(view_date, sales):
    # Calculate totals for all data instead of just the selected date
    total_sales = sum(row["actual_qty_sold"] for row in sales)
    total_revenue = sum(row["actual_qty_sold"] * row["price_per_product"] for row in sales)

    # Get total expenses for all months
    total_expense = fetch_all_expenses_total()

    total_waste = sum(row["waste_returns"] for row in sales)
    unique_days = len({row["date"] for row in sales if row["date"]}) or 1
    avg_daily_waste = round(total_waste / unique_days)
    avg_daily_demand = round(sum(row["actual_qty_sold"] for row in sales) / len(sales)) if sales else 0

    return {
        "totalSalesToday": total_sales,
        "totalRevenueToday": total_revenue,
        "totalExpenseToday": total_expense,
        "totalProfitToday": total_revenue - total_expense,
        "avgDailyWaste": avg_daily_waste,
        "wasteReductionPercent": 15,
        "modelAccuracy": MODEL_METRICS.get("accuracy", 0) if MODEL_METRICS else 0,
        "totalRecords": len(sales),
        "avgDailyDemand": avg_daily_demand,
    }


def get_bread_distribution(sales):
    totals = {}
    for row in sales:
        totals[row["bread_type"]] = totals.get(row["bread_type"], 0) + row["actual_qty_sold"]
    items = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    return [{"name": name, "value": value} for name, value in items]


def get_weekly_plan(sales, days=7):
    averages = {}
    for bread in BREAD_TYPES:
        values = [row["actual_qty_sold"] for row in sales if row["bread_type"] == bread]
        if values:
            averages[bread] = sum(values) / len(values)
    if not averages:
        return []
    plan = []
    today = date.today()
    for i in range(days):
        current = today + timedelta(days=i)
        row = {
            "day": current.strftime("%a"),
            "date": current.strftime("%b %d").replace(" 0", " "),
            "day_date": current.isoformat(),
        }
        for bread, avg in averages.items():
            row[bread] = max(1, int(round(avg * (1.05 + random.random() * 0.08))))
        plan.append(row)
    return plan





def predict_demand(bread_type, target_date, temperature, is_holiday, is_promotion, sales):
    """Generate demand predictions for a given date and bread type(s)."""
    selected = [bread_type] if bread_type != "All" else BREAD_TYPES
    results = []
    # Build per-bread historical sales lookup for lag feature generation
    history = {}
    for row in sales or []:
        bread = row.get("bread_type") or "Pandesal"
        date_key = row.get("date")
        if not date_key:
            continue
        history.setdefault(bread, {})[date_key] = int(row.get("actual_qty_sold") or 0)

    for bread in selected:
        model_features = {
            "bread_type": bread,
            "date": target_date.isoformat(),
            "price_per_product": 4.5,
            "temperature": temperature,
            "is_holiday": int(is_holiday),
            "is_promotion": int(is_promotion),
            "sacks_used": 0,
            "plates_used": 0,
        }
        # Compute historical lag features for the prediction date
        model_features.update(compute_lag_features(model_features, history))
        rolling_features = build_rolling_baseline_features(model_features, history, bread)
        model_features.update(rolling_features)
        if MODEL is not None:
            feature_vector = build_feature_vector(model_features)
            try:
                predicted_value = MODEL.predict([feature_vector])[0]
                predicted = max(10, int(round(np.expm1(predicted_value))))
            except Exception:
                predicted = 120
        else:
            values = [row["actual_qty_sold"] for row in sales if row["bread_type"] == bread]
            base = sum(values) / len(values) if values else 120
            prediction = base
            prediction += 60 if is_holiday else 0
            prediction += 40 if is_promotion else 0
            prediction += 30 if target_date.weekday() >= 5 else 0
            prediction += max(0, 30 - temperature) * 2
            prediction += max(0, rolling_features.get("rolling_trend_30", 0)) * 0.25
            predicted = max(10, int(round(prediction)))
        
        buffer_qty = int(round(predicted * 0.1))
        results.append({
            "breadType": bread,
            "predictedDemand": predicted,
            "recommendedProduction": predicted + buffer_qty,
            "buffer": buffer_qty,
            "confidence": min(99, max(75, 90 + random.randint(-8, 8))),
            "date": target_date.strftime("%Y-%m-%d"),
        })
    return results


def retrain_model(sales):
    metrics = train_model(sales)
    save_model_performance(metrics)
    return metrics


def retrain_model_async(sales):
    """Retrain the model in a background thread to avoid blocking web requests."""
    import threading

    def _worker():
        global MODEL_METRICS
        try:
            print('[DEBUG] retrain_model_async: started')
            metrics = retrain_model(sales)
            MODEL_METRICS = metrics
            print('[DEBUG] retrain_model_async: completed')
        except Exception as e:
            print('[DEBUG] retrain_model_async error:', e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def login_required(view):
    """Decorator that requires the user to be logged in before accessing a page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped



def parse_period_start(period):
    """Return the inclusive start date for a given display period."""
    today = date.today()
    if period == "day":
        return today
    if period == "week":
        return today - timedelta(days=6)
    if period == "month":
        return today - timedelta(days=29)
    return None


def filter_sales(sales, search, filter_date, filter_month, filter_bread, history_period="all"):
    """Filter sales records by search text, date, month, bread type, and history period."""
    filtered = []
    # If filter_month provided, parse it as (year, month)
    month_year = None
    if filter_month:
        try:
            year, month = map(int, filter_month.split("-"))
            month_year = (year, month)
        except ValueError:
            month_year = None

    # If a filter_date is provided and a history_period (daily/weekly/monthly/yearly)
    # compute an explicit start/end range based on that date so server-side
    # filtering uses the selected date as the reference (not "last N days").
    sel_date = parse_date(filter_date) if filter_date else None
    range_start = None
    range_end = None
    if sel_date and history_period in ("daily", "day", "weekly", "week", "monthly", "month", "yearly", "year"):
        # Normalize period names: accept both 'daily' and 'day', etc.
        p = history_period.lower()
        if p.startswith('d'):
            range_start = sel_date
            range_end = sel_date
        elif p.startswith('w'):
            # Week containing sel_date (Monday -> Sunday)
            weekday = sel_date.weekday()  # Monday=0
            monday = sel_date - timedelta(days=weekday)
            sunday = monday + timedelta(days=6)
            range_start = monday
            range_end = sunday
        elif p.startswith('m'):
            range_start = sel_date.replace(day=1)
            # compute last day of month
            next_month = (sel_date.replace(day=28) + timedelta(days=4)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            range_end = last_day
        elif p.startswith('y'):
            range_start = date(sel_date.year, 1, 1)
            range_end = date(sel_date.year, 12, 31)
    else:
        # Fallback to relative history period (e.g., last 7/30 days)
        start_date = parse_period_start(history_period)

    for row in sales:
        # Basic search filter
        if search and search.lower() not in row["bread_type"].lower() and search.lower() not in row.get("date", ""):
            continue

        # If explicit range (based on filter_date + history_period) was computed, use it
        if range_start and range_end:
            row_date = parse_date(row.get("date"))
            if not row_date:
                continue
            if row_date < range_start or row_date > range_end:
                continue
        else:
            # Exact-date filter when no range mode is requested
            if filter_date and row.get("date") != filter_date:
                continue
            # Month filter
            if month_year:
                row_date = parse_date(row.get("date"))
                if not row_date or (row_date.year, row_date.month) != month_year:
                    continue
            # Relative history period (e.g., last N days)
            if not range_start and 'start_date' in locals() and start_date:
                row_date = parse_date(row.get("date"))
                if not row_date or row_date < start_date:
                    continue

        # Bread type filter
        if filter_bread and filter_bread != "All" and row.get("bread_type") != filter_bread:
            continue

        filtered.append(row)

    return filtered


@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        entered_password = request.form.get("password", "").strip()
        if entered_password == LOGIN_PASSWORD:
            session["logged_in"] = True
            session["view_date"] = date.today().isoformat()
            return redirect(url_for("dashboard"))
        error = "Invalid access code. Please try again."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    """Render the dashboard with KPIs, charts, and a weekly plan."""
    global WEEKLY_PLAN, MODEL_METRICS
    
    # Get scope and period parameters from request or session
    view_scope = request.values.get("view_scope") or session.get("view_scope") or "monthly"
    view_date = request.values.get("view_date") or session.get("view_date") or date.today().isoformat()
    view_week = request.values.get("view_week") or session.get("view_week") or ""
    view_month = request.values.get("view_month") or session.get("view_month") or date.today().strftime("%Y-%m")
    
    # Store in session
    session["view_scope"] = view_scope
    session["view_date"] = view_date
    session["view_week"] = view_week
    session["view_month"] = view_month

    if request.method == "POST" and request.form.get("action") == "generate_plan":
        WEEKLY_PLAN = get_weekly_plan(SALES_DATA)

    # Filter sales by scope
    filtered_sales = filter_sales_by_scope(SALES_DATA, view_scope, view_date, view_week, view_month)
    
    # Get the display label
    dashboard_period_label = get_dashboard_period_label(view_scope, view_date, view_week, view_month)
    
    kpis = compute_kpis(view_date, filtered_sales)
    distribution = get_bread_distribution(filtered_sales)[:4]
    chart_data = get_chart_data(filtered_sales)
    monthly_analytics = get_monthly_analytics(filtered_sales)
    future_forecast = get_future_demand_forecast(filtered_sales, days_ahead=14)  # 2 weeks forecast

    # Build ingredient-based alerts (absolute low-stock threshold: <= 5 units)
    ALERT_ABSOLUTE_THRESHOLD = 5
    alerts = []
    try:
        ingredients = fetch_ingredients_from_db() or []
        def fmt_qty(v):
            try:
                f = float(v or 0)
                if f.is_integer():
                    return str(int(f))
                return ('%.2f' % f).rstrip('0').rstrip('.')
            except Exception:
                return str(v or '')

        for ing in ingredients:
            try:
                current = float(ing.get('current_stock') or 0)
            except Exception:
                continue

            # Compute reorder threshold (fallback to absolute threshold when not set)
            try:
                reorder_thr = float(ing.get('reorder_threshold') or 0)
            except Exception:
                reorder_thr = 0

            # Use a safe denominator for progress calculation
            denom = reorder_thr if reorder_thr > 0 else ALERT_ABSOLUTE_THRESHOLD
            if denom <= 0:
                progress_pct = 0
            else:
                progress_pct = int(min(100, max(0, (current / denom) * 100)))

            unit = (ing.get('unit') or '').strip()
            unit_label = f" {unit}" if unit else ''
            current_label = f"{fmt_qty(current)}{unit_label}"
            threshold_label = f"{fmt_qty(reorder_thr if reorder_thr > 0 else ALERT_ABSOLUTE_THRESHOLD)}{unit_label}"

            if current <= 0:
                status_label = "Out of stock"
            elif current <= denom:
                status_label = "Low stock"
            else:
                status_label = "Sufficient"

            # Absolute alert when current stock is <= ALERT_ABSOLUTE_THRESHOLD
            if current <= ALERT_ABSOLUTE_THRESHOLD:
                title = f"Low stock: {ing.get('name')}"
                message = f"{ing.get('name')} stock ({current_label}) is at or below alert level ({threshold_label})."
                alerts.append({
                    "type": "low",
                    "title": title,
                    "message": message,
                    "ingredient": ing,
                    "current_label": current_label,
                    "threshold_label": threshold_label,
                    "progress_pct": progress_pct,
                    "status_label": status_label,
                })
    except Exception:
        alerts = []

    return render_template(
        "dashboard.html",
        active="dashboard",
        view_scope=view_scope,
        view_date=view_date,
        view_week=view_week,
        view_month=view_month,
        dashboard_period_label=dashboard_period_label,
        kpis=kpis,
        distribution=distribution,
        weekly_plan=WEEKLY_PLAN,
        monthly_analytics=monthly_analytics,
        future_forecast=future_forecast,
        alerts=alerts,
        **chart_data,
    )


@app.route("/predict", methods=["GET", "POST"])
@login_required
def predict():
    """Handle demand prediction requests and display prediction results."""
    results = []
    form = {
        "bread_type": "All",
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "temperature": 28,
        "is_holiday": False,
        "is_peak day": False,
    }
    if request.method == "POST":
        form["bread_type"] = request.form.get("bread_type", "All")
        form["date"] = request.form.get("date", form["date"])
        form["temperature"] = int(request.form.get("temperature", 28))
        form["is_holiday"] = request.form.get("is_holiday") == "on"
        form["is_peak day"] = request.form.get("is_peak day") == "on"
        target_date = parse_date(form["date"]) or date.today()
        results = predict_demand(
            form["bread_type"],
            target_date,
            form["temperature"],
            form["is_holiday"],
            form["is_peak day"],
            SALES_DATA,
        )
        # Save predictions to database
        save_predictions(results, target_date)
    return render_template(
        "predict.html",
        active="predict",
        form=form,
        results=results,
        bread_types=BREAD_TYPES,
    )


@app.route("/sales-log", methods=["GET", "POST"])
@login_required
def sales_log():
    """Show sales records and allow manual entry, CSV upload, plus monthly expense management."""
    global WEEKLY_PLAN, MODEL_METRICS, SALES_DATA
    if USE_MYSQL and (not SALES_DATA or not app.config.get("TESTING", False)):
        SALES_DATA = load_sales_data()
    search = request.values.get("search", "")
    filter_date = request.values.get("filter_date", "")
    filter_month = request.values.get("filter_month", "")
    view_month = filter_month or date.today().strftime("%Y-%m")
    filter_bread = request.values.get("filter_bread", "All")
    history_period = request.values.get("history_period", "all")
    expense_categories = EXPENSE_CATEGORIES
    message = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_record":
            new_date = request.form.get("date", date.today().isoformat())
            new_bread = request.form.get("bread_type", "Pandesal")
            produced = int(request.form.get("produced", 0))
            sold = int(request.form.get("sold", 0))
            price = float(request.form.get("price", 4.5))
            expense_amount = float(request.form.get("expense_amount", 0.0))
            record = {
                "date": parse_date(new_date).isoformat() if parse_date(new_date) else date.today().isoformat(),
                "bread_type": new_bread,
                "products_produced": produced,
                "actual_qty_sold": sold,
                "waste_returns": max(0, produced - sold),
                "price_per_product": price,
                "temperature": 30.0,
                "is_holiday": 0,
                "is_peak day": 0,
                "sacks_used": 0,
                "plates_used": 0,
                "expense_amount": expense_amount,
            }
            if save_sales_record(record):
                # Automatically deduct ingredients when sale is logged
                deduction_result = reduce_ingredients_for_sale(new_bread, sold)
                
                SALES_DATA = load_sales_data()
                WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                retrain_model_async(SALES_DATA)
                
                message = "Manual record saved successfully."
                if deduction_result['warnings']:
                    message += " " + " | ".join(deduction_result['warnings'])
            else:
                message = "Failed to save manual record."
        elif action in ("upload_csv", "upload_excel"):
            uploaded_file = request.files.get("file")
            if not uploaded_file or uploaded_file.filename == "":
                message = "Please choose a file to upload."
            else:
                records = parse_csv_upload(uploaded_file)
                if records is None:
                    message = "Failed to parse the uploaded file. Please check the file format."
                elif not records:
                    message = "No valid records were found in the uploaded file."
                else:
                    saved_count = 0
                    deduction_warnings = []
                    for record in records:
                        save_raw_upload_row(record)
                        if save_sales_record(record):
                            saved_count += 1
                            deduction_result = reduce_ingredients_for_sale(
                                record.get("bread_type", "Pandesal"),
                                record.get("actual_qty_sold", 0)
                            )
                            deduction_warnings.extend(deduction_result.get('warnings', []))
                    
                    SALES_DATA = load_sales_data()
                    WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                    retrain_model_async(SALES_DATA)
                    message = f"{saved_count} record(s) uploaded successfully."
                    if deduction_warnings:
                        message += " " + " | ".join(deduction_warnings[:5])
        elif action == "clear_db":
            clear_sales_data()
            WEEKLY_PLAN = []
            retrain_model_async(SALES_DATA)
            message = "All sales records have been cleared."
        elif action == "restore_from_raw_uploads":
            restored = restore_sales_data_from_raw_uploads()
            if restored > 0:
                SALES_DATA = load_sales_data()
                WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                retrain_model_async(SALES_DATA)
                message = f"{restored} uploaded record(s) restored from raw CSV data."
            else:
                message = "No uploaded CSV records were available to restore."
        elif action == "seed_sample_data":
            clear_sales_data()
            for record in generate_sample_sales_records():
                save_sales_record(record)
            SALES_DATA = load_sales_data()
            WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
            retrain_model_async(SALES_DATA)
            message = "Sample sales records have been loaded."
        elif action == "delete_record":
            record_id = request.form.get("record_id")
            if record_id:
                delete_sales_record(record_id)
                SALES_DATA = load_sales_data()  # Reload data after deletion
                WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                retrain_model_async(SALES_DATA)
                message = "Record deleted successfully."
            else:
                message = "Error: Record ID not found."
        elif action == "delete_selected":
            selected_ids = request.form.get("selected_ids", "")
            deleted = 0
            if selected_ids:
                for rid in selected_ids.split(","):
                    rid = rid.strip()
                    if rid:
                        delete_sales_record(rid)
                        deleted += 1
                SALES_DATA = load_sales_data()
                WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
                retrain_model_async(SALES_DATA)
            message = f"{deleted} selected record(s) deleted." if deleted else "No selected records were deleted."
        elif action == "save_batch_records":
            start_date = request.form.get("date", date.today().isoformat())
            scope_days = int(request.form.get("scope_days") or 1)
            bread_names = request.form.getlist("bread_type")
            produced_values = request.form.getlist("produced")
            sold_values = request.form.getlist("sold")
            price_values = request.form.getlist("price")

            bread_inputs = []
            for bread_type, produced, sold, price in zip(bread_names, produced_values, sold_values, price_values):
                bread_inputs.append({
                    "bread_type": bread_type,
                    "produced": produced,
                    "sold": sold,
                    "price": price,
                })

            records = build_batch_entry_records(start_date, scope_days, bread_inputs)
            saved_count = 0
            deduction_warnings = []
            for record in records:
                if save_sales_record(record):
                    saved_count += 1
                    deduction_result = reduce_ingredients_for_sale(record.get("bread_type", "Pandesal"), record.get("actual_qty_sold", 0))
                    deduction_warnings.extend(deduction_result.get("warnings", []))

            SALES_DATA = load_sales_data()
            WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
            retrain_model_async(SALES_DATA)
            message = f"{saved_count} record(s) saved for the selected {scope_days}-day scope."
            if deduction_warnings:
                message += " " + " | ".join(deduction_warnings[:5])
        elif action == "save_monthly_expenses":
            for category in expense_categories:
                amount = float(request.form.get(f"amount_{category}", 0.0) or 0.0)
                note = request.form.get(f"note_{category}", "").strip()
                if amount > 0:
                    save_monthly_expense(filter_month, category, amount, note)
            message = "Monthly expenses saved successfully."
        elif action == "clear_monthly_expenses":
            if USE_MYSQL:
                try:
                    month_date = parse_month(filter_month) or date.today().replace(day=1)
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM monthly_expenses WHERE expense_month = %s", (month_date,))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = "Monthly expenses for this month were cleared."
                except mysql.connector.Error as err:
                    print("MySQL clear monthly expenses error:", err)
                    message = "Error clearing monthly expenses."
    filtered = filter_sales(SALES_DATA, search, filter_date, filter_month, filter_bread, history_period)
    total_dataset = len(SALES_DATA)
    total_filtered = len(filtered)

    # Summary totals should reflect the full uploaded dataset, not just the currently displayed table subset.
    full_sales_dataset = SALES_DATA if SALES_DATA else []
    sold_count = sum(row["actual_qty_sold"] for row in full_sales_dataset)
    produced_count = sum(row["products_produced"] for row in full_sales_dataset)
    total_sales_value = sum(row["actual_qty_sold"] * row["price_per_product"] for row in full_sales_dataset)
    total_waste = sum(row["waste_returns"] for row in full_sales_dataset)

    # Get expenses for the same period as sales data
    if filter_month:
        # If filtering by month, get expenses for that month
        monthly_expense = fetch_monthly_expense_total(filter_month)
        expense_scope = filter_month
    else:
        # If no month filter, calculate all expenses for overall totals
        monthly_expense = fetch_all_expenses_total()
        expense_scope = "All Time"

    current_expenses = fetch_monthly_expenses(view_month)  # For the form, always current month
    profit_value = total_sales_value - monthly_expense
    waste_rate = round((total_waste / produced_count * 100) if produced_count else 0, 1)

    # Debug: log counts and active filters to help diagnose missing data issues
    try:
        print(f"[DEBUG] SALES_DATA={len(SALES_DATA)} total_filtered={len(filtered)} filter_date={filter_date!r} filter_month={filter_month!r} filter_bread={filter_bread!r}")
        # Print a small sample of the loaded sales data to confirm structure
        sample = SALES_DATA[:5]
        try:
            print("[DEBUG] SAMPLE_SALES=", json.dumps(sample, default=str))
        except Exception:
            print("[DEBUG] SAMPLE_SALES (repr)=", repr(sample))
    except Exception:
        pass

    return render_template(
        "sales_log.html",
        active="sales",
        sales=filtered,
        bread_types=BREAD_TYPES,
        bread_prices=BREAD_PRICES,
        search=search,
        filter_date=filter_date,
        filter_bread=filter_bread,
        filter_month=filter_month,
        view_month=view_month,
        message=message,
        today=date.today().isoformat(),
        total_dataset=total_dataset,
        total_filtered=total_filtered,
        sold_count=sold_count,
        produced_count=produced_count,
        total_sales_value=total_sales_value,
        total_waste=total_waste,
        monthly_expense=monthly_expense,
        profit_value=profit_value,
        waste_rate=waste_rate,
        history_period=history_period,
        expense_categories=expense_categories,
        current_expenses=current_expenses,
        expense_scope=expense_scope,
    )


@app.route("/sales-log/add-product", methods=["POST"])
@login_required
def add_sales_product():
    """Add a product to the persistent catalog for future sales entries."""
    global BREAD_TYPES

    bread_type = " ".join(request.form.get("product_name", "").split())
    if not bread_type:
        return jsonify({"success": False, "error": "Enter a product name."}), 400
    if len(bread_type) > 100:
        return jsonify({"success": False, "error": "Product names must be 100 characters or fewer."}), 400
    if any(existing.casefold() == bread_type.casefold() for existing in BREAD_TYPES):
        return jsonify({"success": False, "error": "That product already exists."}), 409

    try:
        default_price = float(request.form.get("product_price", ""))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Enter a valid product price."}), 400
    if not math.isfinite(default_price) or default_price < 0 or default_price > 99999999.99:
        return jsonify({"success": False, "error": "Enter a price from 0 to 99,999,999.99."}), 400
    default_price = round(default_price, 2)

    saved, error = persist_custom_bread_type(bread_type, default_price)
    if not saved:
        status = 409 if error and "already exists" in error else 503
        return jsonify({"success": False, "error": error}), status

    BREAD_TYPES.append(bread_type)
    BREAD_PRICES[bread_type] = default_price
    retrain_model_async(SALES_DATA)
    return jsonify({"success": True, "product": bread_type, "price": default_price})


@app.route("/sales-log/edit/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_sale(record_id):
    """Show and handle edits for a single sales record."""
    global SALES_DATA, WEEKLY_PLAN, MODEL_METRICS
    record = None

    # Try to load the record from the database when available
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT id, date, bread_type, products_produced, actual_qty_sold, waste_returns,
                       price_per_product, temperature, is_holiday, is_promotion, sacks_used, plates_used, expense_amount
                FROM sales_data WHERE id = %s
            """, (record_id,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                record = normalize_record(row)
        except mysql.connector.Error as err:
            print("MySQL fetch single record error:", err)
    else:
        # Fallback: find in-memory record by id
        for r in SALES_DATA:
            if r.get("id") == record_id:
                record = r.copy()
                break

    if not record:
        return redirect(url_for("sales_log"))

    if request.method == "POST":
        # Allow deletion via the edit form
        if request.form.get("action") == "delete_record":
            delete_sales_record(record_id)
            SALES_DATA = load_sales_data()
            WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
            retrain_model_async(SALES_DATA)
            return redirect(url_for("sales_log"))

        # Update record with submitted values
        new_date = request.form.get("date", record.get("date"))
        new_bread = request.form.get("bread_type", record.get("bread_type"))
        produced = int(request.form.get("produced", record.get("products_produced", 0)))
        sold = int(request.form.get("sold", record.get("actual_qty_sold", 0)))
        price = float(request.form.get("price", record.get("price_per_product", 0.0)))
        expense_amount = float(request.form.get("expense_amount", record.get("expense_amount", 0.0)))
        waste = max(0, produced - sold)

        if USE_MYSQL:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE sales_data SET date = %s, bread_type = %s, products_produced = %s,
                                         actual_qty_sold = %s, waste_returns = %s, price_per_product = %s,
                                         expense_amount = %s
                    WHERE id = %s
                """, (
                    (parse_date(new_date).isoformat() if parse_date(new_date) else new_date),
                    new_bread, produced, sold, waste, price, expense_amount, record_id,
                ))
                conn.commit()
                cursor.close()
                conn.close()
            except mysql.connector.Error as err:
                print("MySQL update error:", err)
        else:
            for r in SALES_DATA:
                if r.get("id") == record_id:
                    r.update({
                        "date": (parse_date(new_date).isoformat() if parse_date(new_date) else new_date),
                        "bread_type": new_bread,
                        "products_produced": produced,
                        "actual_qty_sold": sold,
                        "waste_returns": waste,
                        "price_per_product": price,
                        "expense_amount": expense_amount,
                    })
                    break

        SALES_DATA = load_sales_data() if USE_MYSQL else SALES_DATA
        WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
        retrain_model_async(SALES_DATA)
        return redirect(url_for("sales_log"))

    # GET: render the edit form
    return render_template("edit_sale.html", record=record, bread_types=BREAD_TYPES)


@app.route("/production-plan", methods=["GET", "POST"])
@login_required
def production_plan():
    """Show historical production-log records from saved demand predictions."""
    global WEEKLY_PLAN
    message = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "generate_plan":
            target_date = date.today() + timedelta(days=1)
            results = predict_demand("All", target_date, 28, False, False, SALES_DATA)
            save_predictions(results, target_date)
            message = "Prediction results saved to the production log."

    prediction_rows = fetch_prediction_records()
    production_rows = build_production_log_rows(prediction_rows, SALES_DATA)

    return render_template(
        "production_plan.html",
        active="plan",
        production_rows=production_rows,
        message=message,
    )


@app.route("/production-plan/update-outcome", methods=["POST"])
@login_required
def update_production_outcome():
    date_value = (request.form.get("date") or "").strip()
    bread_type = (request.form.get("bread_type") or "").strip()
    field = (request.form.get("field") or "").strip()
    raw_value = (request.form.get("value") or "").strip()

    if field not in ("actual_production", "waste"):
        return jsonify({"success": False, "error": "Choose a valid production field."}), 400
    if not parse_date(date_value) or not bread_type or len(bread_type) > 100:
        return jsonify({"success": False, "error": "Choose a valid production record."}), 400
    if not raw_value.isdecimal():
        return jsonify({"success": False, "error": "Enter a non-negative whole number."}), 400

    value = int(raw_value)
    if value > 2147483647:
        return jsonify({"success": False, "error": "Value is too large."}), 400

    saved, error = save_production_outcome(date_value, bread_type, field, value)
    if not saved:
        status = 404 if error == "No matching sales record exists." else 500
        return jsonify({"success": False, "error": error}), status
    return jsonify({"success": True, "field": field, "value": value})


@app.route("/monthly-expenses", methods=["GET", "POST"])
@login_required
def monthly_expenses():
    """Manage expenses by daily, weekly, or monthly scope."""
    scope_type = (request.values.get("scope_type") or request.form.get("scope_type") or "monthly").strip().lower()
    selected_date = request.values.get("selected_date") or request.form.get("selected_date") or date.today().strftime("%Y-%m-%d")
    selected_week = request.values.get("selected_week") or request.form.get("selected_week") or f"{date.today().isocalendar().year}-W{date.today().isocalendar().week:02d}"
    selected_month = request.values.get("selected_month") or request.form.get("selected_month") or date.today().strftime("%Y-%m")
    message = None

    expense_categories = EXPENSE_CATEGORIES
    scope_context = get_expense_scope_context(scope_type, selected_date=selected_date, selected_week=selected_week, selected_month=selected_month)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_expenses":

            print("===== SAVE BUTTON CLICKED =====")
            print(request.form)

            for category in expense_categories:

                print("CATEGORY:", category)

                raw_amount = request.form.get(f"amount_{category}")
                print("RAW AMOUNT:", raw_amount)

                amount = float(raw_amount or 0.0)
                note = request.form.get(f"note_{category}", "").strip()

                print("AMOUNT:", amount)

                if amount > 0:
                    print("Saving...", category)

                    ok = save_monthly_expense(
                        selected_month,
                        category,
                        amount,
                        note,
                        scope_type=scope_type,
                        period_key=scope_context["period_key"],
                    )

                    print("SAVE RESULT:", ok)

            message = f"{scope_type.title()} expenses saved successfully."
        elif action == "clear_scope":
            if USE_MYSQL:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM monthly_expenses WHERE scope_type = %s AND period_key = %s",
                        (scope_type, scope_context["period_key"])
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = f"All {scope_type} expenses were cleared."
                except mysql.connector.Error as err:
                    print("MySQL clear monthly expenses error:", err)
                    message = "Error clearing expenses."

    current_expenses = fetch_monthly_expenses(selected_month, scope_type=scope_type, period_key=scope_context["period_key"])
    total_expense = sum(item['amount'] for item in current_expenses.values())

    return render_template(
        "monthly_expenses.html",
        active="expenses",
        scope_type=scope_type,
        selected_date=selected_date,
        selected_week=selected_week,
        selected_month=selected_month,
        expense_categories=expense_categories,
        current_expenses=current_expenses,
        total_expense=total_expense,
        message=message,
        scope_context=scope_context,
    )


@app.route("/production-history")
@login_required
def production_history():
    """Show saved production plan history with date filtering and print support."""
    selected_plan_date = request.args.get("plan_date", "")
    plan_sessions = fetch_production_plan_sessions()
    if not selected_plan_date and plan_sessions:
        selected_plan_date = plan_sessions[0]["value"]

    plan_rows = fetch_production_plan_rows(selected_plan_date) if selected_plan_date else []
    history_grid = build_production_history_grid(plan_rows)
    selected_session_label = next(
        (session["label"] for session in plan_sessions if session["value"] == selected_plan_date), ""
    )

    prediction_rows = fetch_prediction_records()
    production_rows = build_production_log_rows(prediction_rows, SALES_DATA)

    return render_template(
        "production_history.html",
        active="plan",
        production_rows=production_rows,
        plan_sessions=plan_sessions,
        selected_plan_date=selected_plan_date,
        selected_session_label=selected_session_label,
        today=date.today().isoformat(),
    )


@app.route("/prediction-records")
@login_required
def prediction_records():
    """Show saved prediction history records."""
    filter_date = request.args.get("filter_date", "")
    records = fetch_prediction_records(filter_date=filter_date)
    return render_template(
        "prediction_records.html",
        active="predictions",
        records=records,
        filter_date=filter_date,
    )


@app.route('/debug/sales-filtered')
def debug_sales_filtered():
    """Return filtered sales and counts using querystring filters for troubleshooting."""
    search = request.args.get('search', '')
    filter_date = request.args.get('filter_date', '')
    filter_month = request.args.get('filter_month', '')
    filter_bread = request.args.get('filter_bread', 'All')
    history_period = request.args.get('history_period', 'all')
    # optional: support returning a limited or full result set for printing/debugging
    limit = request.args.get('limit', None)
    full = request.args.get('all', '').lower() in ('1', 'true', 'yes')
    try:
        filtered = filter_sales(SALES_DATA, search, filter_date, filter_month, filter_bread, history_period)
        # determine how many records to include in the response
        if full:
            out_list = filtered
        elif limit:
            try:
                n = int(limit)
                out_list = filtered[:n]
            except Exception:
                out_list = filtered[:50]
        else:
            out_list = filtered[:50]

        return jsonify({
            'filtered_count': len(filtered),
            'sample_count': len(out_list),
            'sample': out_list,
            'applied': {
                'search': search,
                'filter_date': filter_date,
                'filter_month': filter_month,
                'filter_bread': filter_bread,
                'history_period': history_period,
            }
        })
    except Exception as err:
        return jsonify({'error': str(err)}), 500


@app.route("/model-performance", methods=["GET", "POST"])
@login_required
def model_performance():
    """Show ML model metrics and support retraining the model from sales data."""
    global MODEL_METRICS
    if request.method == "POST" and request.form.get("action") == "retrain":
        retrain_model_async(SALES_DATA)
    
    # Try to load latest metrics from database
    db_metrics = fetch_latest_model_performance()
    if db_metrics:
        MODEL_METRICS = db_metrics
    
    # Swap the displayed order/values for Holiday and Bread Type as requested
    feature_weights = [
        {"feature": "Bread Type", "weight": 62},
        {"feature": "Peak Day", "weight": 51},
        {"feature": "Weekend", "weight": 38},
        {"feature": "Temperature", "weight": 24},
        {"feature": "Holiday", "weight": 18},
    ]
    return render_template(
        "model_performance.html",
        active="model",
        metrics=MODEL_METRICS,
        feature_weights=feature_weights,
    )


@app.route("/about")
@login_required
def about():
    """Render the about page for the application."""
    return render_template("about.html", active="about")


# --- Ingredients inventory helpers and routes ---
def fetch_ingredients_from_db():
    """Fetch ingredients from the database (or fallback to in-memory list)."""
    if not USE_MYSQL:
        return INGREDIENTS
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, current_stock, unit, reorder_threshold, last_restock FROM ingredients ORDER BY name")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        # Normalize numeric/date types for JSON rendering
        for r in rows:
            try:
                r["current_stock"] = float(r["current_stock"]) if r.get("current_stock") is not None else 0
            except Exception:
                r["current_stock"] = 0
            try:
                r["reorder_threshold"] = float(r["reorder_threshold"]) if r.get("reorder_threshold") is not None else 0
            except Exception:
                r["reorder_threshold"] = 0
            if isinstance(r.get("last_restock"), datetime):
                r["last_restock"] = r["last_restock"].isoformat()
        return rows
    except mysql.connector.Error as err:
        print("MySQL fetch ingredients error:", err)
        return []


def seed_default_ingredients_to_db():
    """Insert DEFAULT_INGREDIENTS into the ingredients table (or in-memory list).
    Returns (True, inserted_count) on success or (False, error_message) on failure.
    """
    inserted = 0
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM ingredients")
            existing = {row[0] for row in cursor.fetchall()}
            for ing in DEFAULT_INGREDIENTS:
                name = ing.get('name')
                if name in existing:
                    continue
                cursor.execute(
                    "INSERT INTO ingredients (name, current_stock, unit, reorder_threshold) VALUES (%s, %s, %s, %s)",
                    (name, ing.get('current_stock', 0), ing.get('unit', ''), ing.get('reorder_threshold', 0))
                )
                inserted += 1
            conn.commit()
            cursor.close()
            conn.close()
            return True, inserted
        except mysql.connector.Error as err:
            print('MySQL seed error:', err)
            return False, str(err)
    else:
        global INGREDIENTS
        max_id = max([int(i.get('id', 0)) for i in INGREDIENTS], default=0) if INGREDIENTS else 0
        for ing in DEFAULT_INGREDIENTS:
            if any(i.get('name') == ing.get('name') for i in INGREDIENTS):
                continue
            max_id += 1
            row = {
                'id': max_id,
                'name': ing.get('name'),
                'current_stock': float(ing.get('current_stock', 0)),
                'unit': ing.get('unit', ''),
                'reorder_threshold': float(ing.get('reorder_threshold', 0)),
                'last_restock': None,
            }
            INGREDIENTS.append(row)
            inserted += 1
        return True, inserted


@app.route("/ingredients", methods=["GET", "POST"]) 
@login_required
def ingredients_inventory():
    """Render the Ingredients Inventory page with summary cards and allow saving ingredient expenses."""
    ingredients = fetch_ingredients_from_db()
    # Auto-seed default ingredients when the DB/list is empty so inline adjustments work
    if not ingredients and DEFAULT_INGREDIENTS:
        ok, result = seed_default_ingredients_to_db()
        if ok:
            inserted = result
            if inserted:
                ingredients = fetch_ingredients_from_db()
                message = f"Seeded {inserted} default ingredient(s)."
        else:
            # result contains error message
            message = f"Failed to seed default ingredients: {result}"
    message = None

    scope_type = (request.values.get("scope_type") or request.form.get("scope_type") or "monthly").strip().lower()
    selected_date = request.values.get("selected_date") or request.form.get("selected_date") or date.today().strftime("%Y-%m-%d")
    selected_week = request.values.get("selected_week") or request.form.get("selected_week") or f"{date.today().isocalendar().year}-W{date.today().isocalendar().week:02d}"
    selected_month = request.values.get("selected_month") or request.form.get("selected_month") or date.today().strftime("%Y-%m")
    scope_context = get_expense_scope_context(scope_type, selected_date=selected_date, selected_week=selected_week, selected_month=selected_month)

    if request.method == "POST":
        ensure_monthly_expenses_table_schema()
        action = request.form.get("action")
        if action == "save_expenses":
            for category in EXPENSE_CATEGORIES:
                try:
                    amount = float(request.form.get(f"amount_{category}", 0.0) or 0.0)
                except Exception:
                    amount = 0.0
                note = (
                    request.form.get(f"note_{category}")
                    or request.form.get(f"unit_{category}")
                    or ""
                ).strip()
                if amount > 0:
                    ok = save_monthly_expense(
                        selected_month,
                        category,
                        amount,
                        note,
                        scope_type=scope_type,
                        period_key=scope_context["period_key"],
                    )
                    if not ok:
                        message = f"Error saving {category}: database error."
                        break
            message = f"{scope_type.title()} expenses saved successfully."
        elif action == "clear_expenses":
            if not USE_MYSQL:
                MONTHLY_EXPENSES.pop((scope_type, scope_context["period_key"]), None)
                message = f"All {scope_type} expenses were cleared."
            elif USE_MYSQL:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM monthly_expenses WHERE scope_type = %s AND period_key = %s",
                        (scope_type, scope_context["period_key"]),
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    message = f"All {scope_type} expenses were cleared."
                except mysql.connector.Error as err:
                    print("MySQL clear monthly expenses error:", err)
                    message = "Error clearing expenses."

    total_ingredients = len(ingredients)
    low_stock_count = sum(1 for i in ingredients if float(i.get("current_stock", 0)) <= float(i.get("reorder_threshold", 0)))
    category_units = {
        ingredient.get("name"): ingredient.get("unit") or "kg"
        for ingredient in ingredients
        if ingredient.get("name")
    }
    for default_ingredient in DEFAULT_INGREDIENTS:
        category_units.setdefault(default_ingredient.get("name"), default_ingredient.get("unit") or "kg")
    last_restock_dt = None
    for i in ingredients:
        if i.get("last_restock"):
            d = parse_datetime(i.get("last_restock"))
            if d and (last_restock_dt is None or d > last_restock_dt):
                last_restock_dt = d
    last_restock_label = format_date(last_restock_dt) if last_restock_dt else ""

    current_expenses = fetch_monthly_expenses(
        selected_month,
        scope_type=scope_type,
        period_key=scope_context["period_key"],
    )
    monthly_expense = fetch_monthly_expense_total(
        selected_month,
        scope_type=scope_type,
        period_key=scope_context["period_key"],
    )
    expense_scope = scope_context["period_key"]

    # Fetch expense history for the selected scope
    expense_history = []
    if not USE_MYSQL:
        # For in-memory storage, build history from MONTHLY_EXPENSES
        bucket = MONTHLY_EXPENSES.get((scope_type, scope_context["period_key"]), {})
        for category, entry in bucket.items():
            expense_history.append({
                "scope_type": scope_type,
                "period_key": scope_context["period_key"],
                "category": category,
                "amount": float(entry.get("amount", 0.0)),
                "note": entry.get("note", ""),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
    elif USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """SELECT scope_type, period_key, category, amount, note, created_at 
                   FROM monthly_expenses 
                   WHERE scope_type = %s AND period_key = %s 
                   ORDER BY created_at DESC""",
                (scope_type, scope_context["period_key"])
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            # Convert datetime objects to strings for template rendering
            for row in rows:
                if row.get('created_at'):
                    if isinstance(row['created_at'], datetime):
                        row['created_at'] = row['created_at'].strftime("%Y-%m-%d %H:%M:%S")
            expense_history = rows
        except mysql.connector.Error as err:
            print("MySQL fetch expense history error:", err)
            expense_history = []

    return render_template(
        "ingredients_inventory.html",
        active="ingredients",
        ingredients=ingredients,
        total_ingredients=total_ingredients,
        low_stock_count=low_stock_count,
        last_restock_label=last_restock_label,
        default_ingredients=DEFAULT_INGREDIENTS,
        expense_categories=EXPENSE_CATEGORIES,
        current_expenses=current_expenses,
        selected_date=selected_date,
        selected_week=selected_week,
        selected_month=selected_month,
        scope_type=scope_type,
        scope_context=scope_context,
        monthly_expense=monthly_expense,
        expense_scope=expense_scope,
        expense_history=expense_history,
        category_units=category_units,
        message=message,
    )


@app.route('/ingredients/seed-default', methods=['POST'])
@login_required
def seed_default_ingredients():
    """Seed the ingredients table (or in-memory list) with DEFAULT_INGREDIENTS.
    Returns JSON with the number of inserted rows.
    """
    inserted = 0
    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM ingredients")
            existing = {row[0] for row in cursor.fetchall()}
            for ing in DEFAULT_INGREDIENTS:
                name = ing.get('name')
                if name in existing:
                    continue
                cursor.execute(
                    "INSERT INTO ingredients (name, current_stock, unit, reorder_threshold) VALUES (%s, %s, %s, %s)",
                    (name, ing.get('current_stock', 0), ing.get('unit', ''), ing.get('reorder_threshold', 0))
                )
                inserted += 1
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({'success': True, 'inserted': inserted})
        except mysql.connector.Error as err:
            print('MySQL seed error:', err)
            return jsonify({'success': False, 'error': str(err)}), 500
    else:
        # in-memory fallback
        global INGREDIENTS
        max_id = max([int(i.get('id', 0)) for i in INGREDIENTS], default=0) if INGREDIENTS else 0
        for ing in DEFAULT_INGREDIENTS:
            if any(i.get('name') == ing.get('name') for i in INGREDIENTS):
                continue
            max_id += 1
            row = {
                'id': max_id,
                'name': ing.get('name'),
                'current_stock': float(ing.get('current_stock', 0)),
                'unit': ing.get('unit', ''),
                'reorder_threshold': float(ing.get('reorder_threshold', 0)),
                'last_restock': None,
            }
            INGREDIENTS.append(row)
            inserted += 1
        return jsonify({'success': True, 'inserted': inserted})


@app.route('/api/ingredients/<int:ingredient_id>/adjust', methods=['POST'])
@login_required
def adjust_ingredient(ingredient_id):
    """Adjust the stock level for an ingredient (stock-in or stock-out).
    Expects JSON: { amount: number, type: 'in'|'out', note?: string }
    """
    data = request.get_json(silent=True) or request.form or {}
    try:
        amount = float(data.get('amount', 0))
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid amount'}), 400
    ttype = (data.get('type') or 'in')
    note = data.get('note', '') or ''

    if USE_MYSQL:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if ttype == 'out':
                cursor.execute("UPDATE ingredients SET current_stock = GREATEST(0, current_stock - %s) WHERE id = %s", (amount, ingredient_id))
                change_amt = -abs(amount)
            else:
                cursor.execute("UPDATE ingredients SET current_stock = current_stock + %s, last_restock = %s WHERE id = %s", (amount, datetime.now(), ingredient_id))
                change_amt = abs(amount)
            cursor.execute("INSERT INTO ingredient_transactions (ingredient_id, change_amount, transaction_type, note) VALUES (%s, %s, %s, %s)", (ingredient_id, change_amt, ttype, note))
            conn.commit()
            cursor.close()
            # fetch updated ingredient
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, name, current_stock, unit, reorder_threshold, last_restock FROM ingredients WHERE id = %s", (ingredient_id,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                try:
                    row['current_stock'] = float(row.get('current_stock') or 0)
                except Exception:
                    row['current_stock'] = 0
                try:
                    row['reorder_threshold'] = float(row.get('reorder_threshold') or 0)
                except Exception:
                    row['reorder_threshold'] = 0
                if isinstance(row.get('last_restock'), datetime):
                    row['last_restock'] = row['last_restock'].isoformat()
                return jsonify({'success': True, 'ingredient': row})
            return jsonify({'success': False, 'error': 'Ingredient not found'}), 404
        except mysql.connector.Error as err:
            print('MySQL ingredient adjust error:', err)
            return jsonify({'success': False, 'error': str(err)}), 500
    else:
        # in-memory fallback
        for ing in INGREDIENTS:
            if int(ing.get('id', 0)) == int(ingredient_id):
                if ttype == 'out':
                    ing['current_stock'] = max(0, float(ing.get('current_stock', 0)) - amount)
                else:
                    ing['current_stock'] = float(ing.get('current_stock', 0)) + amount
                    ing['last_restock'] = datetime.now().isoformat()
                return jsonify({'success': True, 'ingredient': ing})
        return jsonify({'success': False, 'error': 'Ingredient not found'}), 404


def create_app():
    # Ensure DB/tables exist, clean duplicates, and populate module-level caches
    global SALES_DATA, WEEKLY_PLAN, MODEL_METRICS
    init_db()
    for bread_type, default_price in load_custom_bread_types().items():
        if not any(existing.casefold() == bread_type.casefold() for existing in BREAD_TYPES):
            BREAD_TYPES.append(bread_type)
        existing_bread_type = next(
            existing for existing in BREAD_TYPES if existing.casefold() == bread_type.casefold()
        )
        BREAD_PRICES[existing_bread_type] = default_price
    SALES_DATA = load_sales_data()
    WEEKLY_PLAN = get_weekly_plan(SALES_DATA)
    db_metrics = fetch_latest_model_performance()
    if db_metrics:
        MODEL_METRICS = db_metrics
    else:
        MODEL_METRICS = retrain_model(SALES_DATA)

    # Keep compatibility with older /prediction_system URLs while serving the
    # app from the site root by default.
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, COMPAT_PREFIX)
    return app


if __name__ == "__main__":
    create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8501)), debug=app.config.get("DEBUG", True))


application = create_app()
