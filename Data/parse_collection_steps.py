import json
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
                    print(f"⚠️ Skipping malformed step for host {host_id}: {step}")
                    continue
                results.append({
                    "Ability Name": step.get("name") or "-",
                    "TTP": step.get("attack", {}).get("technique_id") or "-",
                    "Status": map_status(step.get("status")),
                    "Command": step.get("command") or "-",
                    "Output": step.get("output", {}).get("stdout") or "-",
                    "Error": step.get("output", {}).get("stderr") or "-"
                })
            break  # Only process first host with steps
    return results

def save_to_excel(data, filename):
    wb = Workbook()
    ws = wb.active
    ws.title = "Tactic Steps"

    headers = list(data[0].keys())
    ws.append(headers)

    for row in data:
        ws.append([sanitize_excel(row.get(h, "-")) for h in headers])

    wb.save(filename)
    print(f"\n✅ Saved to: {filename}")

def main(): 
    # resource-development\rd - 1_report.json
    input_paths = [r"resource-development\rd - 1_report.json", 
                   r"resource-development\rd - 2_report.json",
                   r"resource-development\rd - 3_report.json",
                   r"resource-development\rd - 4_report.json",
                   r"resource-development\rd - 5_report.json",    
                   ]
    
    output_paths = [r"resource-development\rd - 1_report.xlsx", 
                   r"resource-development\rd - 2_report.xlsx",
                   r"resource-development\rd - 3_report.xlsx",
                   r"resource-development\rd - 4_report.xlsx",
                   r"resource-development\rd - 5_report.xlsx",    
                   ]
    for input_path, output_path in zip(input_paths, output_paths):
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        extracted = extract_steps(data)

        if extracted:
            save_to_excel(extracted, output_path)
        else:
            print("⚠️ No valid steps found.")

if __name__ == "__main__":
    main()
