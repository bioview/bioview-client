import json
from typing import Dict


def load_json_file(json_file) -> Dict:
    try:
        with open(json_file, encoding="utf-8") as f:
            json_data = json.load(f)  # Get dictionary

            # Validate for format correctness
            if not isinstance(json_data, dict):
                raise ValueError("JSON must be a dictionary at the top level.")

            return json_data
    except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
        raise ValueError(f"Error loading JSON: {e}") from e
