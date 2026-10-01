"""
Seed script — creates and populates the rideshare.db SQLite database from CSVs.

Usage: uv run python feed_db.py
"""

import os
import csv
import sqlite3
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

DB_FILE = os.getenv("SQLITE_DB", "rideshare.db")
CSV_DIR = "data"

# ============================================================
# DATABASE CONNECTION
# ============================================================

conn = sqlite3.connect(DB_FILE)
conn.execute("PRAGMA foreign_keys = ON")
cursor = conn.cursor()
print(f"Connected to SQLite: {DB_FILE}")

# ============================================================
# CREATE TABLES
# ============================================================

create_tables_sql = """

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    city TEXT,
    province TEXT,
    user_type TEXT NOT NULL,
    signup_date DATE,
    is_active BOOLEAN
);

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id INTEGER PRIMARY KEY,
    driver_id INTEGER NOT NULL,
    make TEXT,
    model TEXT,
    year INTEGER,
    license_plate TEXT UNIQUE,
    color TEXT,
    is_active BOOLEAN,
    CONSTRAINT fk_vehicle_driver
        FOREIGN KEY (driver_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS rides (
    ride_id INTEGER PRIMARY KEY,
    rider_id INTEGER NOT NULL,
    driver_id INTEGER NOT NULL,
    requested_at TIMESTAMP,
    pickup_time TIMESTAMP,
    dropoff_time TIMESTAMP,
    pickup_latitude DECIMAL(9,6),
    pickup_longitude DECIMAL(9,6),
    dropoff_latitude DECIMAL(9,6),
    dropoff_longitude DECIMAL(9,6),
    distance_km DECIMAL(10,2),
    fare DECIMAL(10,2),
    surge_multiplier DECIMAL(4,2),
    status TEXT,
    cancellation_reason TEXT,
    CONSTRAINT fk_ride_rider
        FOREIGN KEY (rider_id) REFERENCES users(user_id),
    CONSTRAINT fk_ride_driver
        FOREIGN KEY (driver_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY,
    ride_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount DECIMAL(10,2),
    payment_method TEXT,
    payment_status TEXT,
    transaction_id TEXT UNIQUE,
    payment_time TIMESTAMP,
    CONSTRAINT fk_payment_ride
        FOREIGN KEY (ride_id) REFERENCES rides(ride_id),
    CONSTRAINT fk_payment_user
        FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS ratings (
    rating_id INTEGER PRIMARY KEY,
    ride_id INTEGER NOT NULL,
    rider_id INTEGER NOT NULL,
    driver_id INTEGER NOT NULL,
    rating INTEGER,
    comment TEXT,
    rated_at TIMESTAMP,
    CONSTRAINT fk_rating_ride
        FOREIGN KEY (ride_id) REFERENCES rides(ride_id),
    CONSTRAINT fk_rating_rider
        FOREIGN KEY (rider_id) REFERENCES users(user_id),
    CONSTRAINT fk_rating_driver
        FOREIGN KEY (driver_id) REFERENCES users(user_id),
    CONSTRAINT chk_rating CHECK (rating BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS idx_vehicles_driver_id ON vehicles(driver_id);
CREATE INDEX IF NOT EXISTS idx_rides_rider_id ON rides(rider_id);
CREATE INDEX IF NOT EXISTS idx_rides_driver_id ON rides(driver_id);
CREATE INDEX IF NOT EXISTS idx_rides_requested_at ON rides(requested_at);
CREATE INDEX IF NOT EXISTS idx_rides_status ON rides(status);
CREATE INDEX IF NOT EXISTS idx_payments_ride_id ON payments(ride_id);
CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_ratings_ride_id ON ratings(ride_id);
CREATE INDEX IF NOT EXISTS idx_ratings_driver_id ON ratings(driver_id);

"""

cursor.executescript(create_tables_sql)
print("Tables created successfully")


# ============================================================
# LOAD CSV INTO SQLITE
# ============================================================

def load_csv(table_name, csv_file, columns):
    file_path = os.path.join(CSV_DIR, csv_file)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"CSV file not found: {file_path}")

    column_names = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    insert_sql = f"INSERT OR IGNORE INTO {table_name} ({column_names}) VALUES ({placeholders})"

    rows = []
    with open(file_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            values = []
            for column in columns:
                value = row.get(column)
                if value == "":
                    value = None
                values.append(value)
            rows.append(values)

    cursor.executemany(insert_sql, rows)
    print(f"Loaded {csv_file}: {len(rows):,} records")


# ============================================================
# LOAD ALL TABLES
# ============================================================

load_csv("users", "users.csv", [
    "user_id", "first_name", "last_name", "email", "phone",
    "city", "province", "user_type", "signup_date", "is_active",
])

load_csv("vehicles", "vehicles.csv", [
    "vehicle_id", "driver_id", "make", "model", "year",
    "license_plate", "color", "is_active",
])

load_csv("rides", "rides.csv", [
    "ride_id", "rider_id", "driver_id", "requested_at", "pickup_time",
    "dropoff_time", "pickup_latitude", "pickup_longitude",
    "dropoff_latitude", "dropoff_longitude", "distance_km", "fare",
    "surge_multiplier", "status", "cancellation_reason",
])

load_csv("payments", "payments.csv", [
    "payment_id", "ride_id", "user_id", "amount", "payment_method",
    "payment_status", "transaction_id", "payment_time",
])

load_csv("ratings", "ratings.csv", [
    "rating_id", "ride_id", "rider_id", "driver_id",
    "rating", "comment", "rated_at",
])


# ============================================================
# VERIFY & COMMIT
# ============================================================

tables = ["users", "vehicles", "rides", "payments", "ratings"]
print("\nRecord counts:")
print("-" * 40)
for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"{table:<15} {count:>10,}")

conn.commit()
print("\nData loaded successfully!")

cursor.close()
conn.close()
print("SQLite connection closed.")
