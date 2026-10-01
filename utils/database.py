import sqlite3
import os


class DatabaseUtil:
    """Lightweight SQLite helper — no import-time side effects."""

    def __init__(self, db_path: str = "rideshare.db"):
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def schema_details(self) -> str:
        """Return a human-readable dump of every table's columns + sample rows."""
        connection = None
        cursor = None
        try:
            connection = sqlite3.connect(self.db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.cursor()

            schema_info = "Database Schema: SQLite\n"

            cursor.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name;
                """
            )
            tables = cursor.fetchall()

            for (table_name,) in tables:
                schema_info += f"\nTable: {table_name}\n"

                cursor.execute(f"PRAGMA table_info('{table_name}');")
                for col in cursor.fetchall():
                    schema_info += f"  Column: {col[1]}, Data Type: {col[2]}\n"

                cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 5;')
                schema_info += "  Sample Data:\n"
                for row in cursor.fetchall():
                    schema_info += f"    {row}\n"

            return schema_info

        except Exception as e:
            return f"Error fetching schema details: {e}"

        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_sql(self, query: str) -> str | None:
        """Execute a read query and return stringified results."""
        connection = None
        cursor = None
        try:
            connection = sqlite3.connect(self.db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.cursor()
            cursor.execute(query)

            if cursor.description is not None:
                result = cursor.fetchall()
            else:
                result = []

            connection.commit()
            return str(result)

        except Exception as e:
            if connection:
                connection.rollback()
            return f"Error executing query: {e}"

        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
