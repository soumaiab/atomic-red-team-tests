import argparse
import json
import time
from pathlib import Path

import xlsxwriter
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

# xlsxwriter's default mode stages each worksheet as its own file in %TEMP%
# before zipping - antivirus real-time scanning quarantines those temp files
# outright when the cell content is raw attack commands (observed: Windows
# Defender flagging them as Trojan:VBS/Boxter.HAB!MTB). in_memory=True builds
# the workbook entirely in RAM and writes the .xlsx in one shot, which avoids
# ever creating that scannable intermediate file.
EXCEL_MAX_CELL_LENGTH = 32767

# The final file write can still occasionally lose a race (e.g. the .xlsx is
# open in Excel, or a one-off AV scan of the finished file). Retry a few times.
SAVE_RETRIES = 5
SAVE_RETRY_DELAY_SECONDS = 1


def map_status(code):
    return {0: "success", 1: "failure"}.get(code, "timeout")


def sanitize_excel(value):
    if value is None:
        return "-"
    if not isinstance(value, str):
        return value
    # Remove characters Excel cannot store (control chars)
    value = ILLEGAL_CHARACTERS_RE.sub("", value)
    return value[:EXCEL_MAX_CELL_LENGTH]


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
    headers = headers or list(data[0].keys())
    rows = [[sanitize_excel(row.get(h, "-")) for h in headers] for row in data]

    for attempt in range(1, SAVE_RETRIES + 1):
        try:
            wb = xlsxwriter.Workbook(str(filename), {"in_memory": True})
            ws = wb.add_worksheet("Tactic Steps")
            for col, header in enumerate(headers):
                ws.write_string(0, col, header)
            for r, row in enumerate(rows, start=1):
                for c, value in enumerate(row):
                    if isinstance(value, str):
                        ws.write_string(r, c, value)
                    else:
                        ws.write(r, c, value)
            wb.close()
            break
        except OSError:
            if attempt == SAVE_RETRIES:
                raise
            print(f"[!] Save failed (attempt {attempt}/{SAVE_RETRIES}) - retrying: {filename}")
            time.sleep(SAVE_RETRY_DELAY_SECONDS)

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
