import os
import requests
import pandas as pd


class ETLTools:
    """Extract-Load and Transform-Load utilities for the ETL agent."""

    def extract_load(self, url: str, output_folder: str, format: str) -> str:
        """
        Extract data from an API endpoint and save to disk.

        Args:
            url: API endpoint URL.
            output_folder: Destination folder (relative to project root).
            format: Output format — csv, json, or parquet.
        """
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        output_folder = os.path.join(project_root, output_folder)

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Fix: os.path.join (was os.path.json in original)
            filename = os.path.join(output_folder, f"extracted_data.{format}")
            os.makedirs(output_folder, exist_ok=True)

            results_key = "results" if "results" in data else None
            raw = data[results_key] if results_key else data
            df = pd.json_normalize(raw) if isinstance(raw, list) else pd.json_normalize([raw])

            if format == "csv":
                df.to_csv(filename, index=False)
            elif format == "json":  # Fix: was "jason" in original
                df.to_json(filename, orient="records", lines=True)
            elif format == "parquet":
                df.to_parquet(filename, index=False)
            else:
                return f"Unsupported file format: {format}"

            return f"Data successfully extracted and saved to {filename}"

        except requests.exceptions.RequestException as e:
            return f"Failed to extract data: {e}"

    def transform_load_context(self, file_path: str) -> str:
        """
        Read the first 3 rows of a data file to give the LLM context.
        """
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == ".csv":
                df = pd.read_csv(file_path)
            elif ext == ".json":
                df = pd.read_json(file_path, lines=True)
            elif ext == ".parquet":
                df = pd.read_parquet(file_path)
            else:
                return f"Unsupported file format: {ext}"
            return str(df.head(3))
        except Exception as e:
            return f"Error reading file: {e}"

    def execute_code(self, code: str) -> str:
        """
        Execute a Python/Pandas code snippet in-process.
        """
        try:
            exec(code)  # noqa: S102
            return "Code executed successfully"
        except Exception as e:
            # Fix: was missing f-string prefix in original
            return f"Failed to execute code: {e}"
