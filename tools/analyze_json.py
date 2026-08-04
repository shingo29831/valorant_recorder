import json
import sys
import os

def get_sample_value(val):
    if val is None:
        return "null"
    elif isinstance(val, str):
        # 長すぎる文字列は切り詰める
        sample = val if len(val) <= 50 else val[:47] + "..."
        return f'"{sample}"'
    else:
        return str(val)

def analyze_structure(data, indent=0):
    spaces = "  " * indent
    if isinstance(data, dict):
        print(spaces + "{")
        for key, value in data.items():
            print(spaces + f'  "{key}": ', end="")
            if isinstance(value, (dict, list)):
                print()
                analyze_structure(value, indent + 1)
            else:
                type_name = type(value).__name__
                sample = get_sample_value(value)
                print(f"<{type_name}> (sample: {sample})")
        print(spaces + "}")
    elif isinstance(data, list):
        print(spaces + "[")
        if len(data) > 0:
            print(spaces + "  // List of " + str(len(data)) + " items. Showing first item structure:")
            analyze_structure(data[0], indent + 1)
        else:
            print(spaces + "  // Empty list")
        print(spaces + "]")
    else:
        type_name = type(data).__name__
        sample = get_sample_value(data)
        print(spaces + f"<{type_name}> (sample: {sample})")

def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/analyze_json.py <path_to_json_file>")
        print("Example: python tools/analyze_json.py records/match_20260804_082040.json")
        return

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"--- Structure of {os.path.basename(filepath)} ---")
        analyze_structure(data)
    except Exception as e:
        print(f"Error parsing JSON: {e}")

if __name__ == "__main__":
    main()