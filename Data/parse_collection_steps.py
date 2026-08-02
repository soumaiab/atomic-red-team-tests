import argparse
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


def map_status(code):
    return {0: "success", 1: "failure"}.get(code, "timeout")


def sanitize_excel(value):
    if value is None:
        return "-"
    if not isinstance(value, str):
        return value
    # Remove characters Excel/openpyxl cannot store (control chars)
    return ILLEGAL_CHARACTERS_RE.sub("", value)


def extract_steps(data):
    results = []
    for host_id, step_container in data.get("steps", {}).items():
        step_list = step_container.get("steps", [])
        if isinstance(step_list, list) and step_list:
            for step in step_list:
                if not isinstance(step, dict):
                    print(f"[!] Skipping malformed step for host {host_id}: {step}")
                    continue
                results.append({
                    "Ability Name": step.get("name") or "-",
                    "TTP": step.get("attack", {}).get("technique_id") or "-",
                    "Status": map_status(step.get("status")),
                    "Command": step.get("command") or "-",
                    "Output": step.get("output", {}).get("stdout") or "-",
                    "Error": step.get("output", {}).get("stderr") or "-"
                })
    return results


def save_to_excel(data, filename, headers=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Tactic Steps"

    headers = headers or list(data[0].keys())
    ws.append(headers)

    for row in data:
        ws.append([sanitize_excel(row.get(h, "-")) for h in headers])

    wb.save(filename)
    print(f"\n[ok] Saved to: {filename}")


def main():
    parser = argparse.ArgumentParser(description="Extract Caldera report.json collection steps into an Excel sheet.")
    parser.add_argument("report_json_path", type=Path, help="Path to the *_report.json file")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output .xlsx path (default: alongside input)")
    args = parser.parse_args()

    output_path = args.output or args.report_json_path.with_suffix(".xlsx")

    with open(args.report_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    extracted = extract_steps(data)
    if extracted:
        save_to_excel(extracted, output_path)
    else:
        print("[!] No valid steps found.")


if __name__ == "__main__":
    main()
