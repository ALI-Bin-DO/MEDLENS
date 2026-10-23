"""Load the Hospital Management CSVs from Data/raw/ into the medlens MySQL database.

Run database/schema.sql first. Credentials come from the .env file in the project root.
Safe to re-run: existing rows are deleted before loading.
"""

import csv
import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "Data" / "raw"

# Parents first, so foreign keys always point at rows that already exist.
TABLES = ["patients", "doctors", "appointments", "treatments", "billing"]


def read_csv(table):
    with open(RAW_DIR / f"{table}.csv", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        # Empty cells become NULL instead of empty strings.
        rows = [tuple(value.strip() or None for value in row.values()) for row in reader]
    return columns, rows


def main():
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=os.getenv("MYSQL_DATABASE", "medlens"),
        )
    except KeyError as missing:
        sys.exit(f"Missing {missing} in .env - copy .env.example to .env and fill it in.")

    cursor = conn.cursor()
    try:
        # Children first when deleting, so foreign keys don't block it.
        for table in reversed(TABLES):
            cursor.execute(f"DELETE FROM {table}")

        for table in TABLES:
            columns, rows = read_csv(table)
            placeholders = ", ".join(["%s"] * len(columns))
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            cursor.executemany(sql, rows)
            print(f"{table}: loaded {cursor.rowcount} rows")

        conn.commit()  # all tables load together, or none do
        print("Done.")
    except mysql.connector.Error:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
