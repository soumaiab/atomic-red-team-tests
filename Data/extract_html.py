import argparse
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag

from parse_collection_steps import save_to_excel

HEADERS = ["Ability Name", "TTP", "Status", "Command", "Output", "Error"]


def _label_box_text(row: Tag, label_substring: str) -> str:
    """Find a <label> containing label_substring, then read the <pre> text
    from its sibling .box div (if present) within the same row."""
    label = row.find("label", string=lambda s: s and label_substring in s)
    if not label:
        return ""
    box = label.find_next_sibling("div", class_="box")
    if not box:
        return ""
    pre = box.find("pre")
    if not pre:
        return ""
    return pre.get_text("\n", strip=True)


def extract_rows(html_path: Path) -> list[dict]:
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    table = soup.find("table", {"id": "link-table"})
    rows = table.find_all("tr")

    results = []
    for row in rows:
        cols = row.find_all("td", recursive=False)
        if len(cols) < 8:
            continue  # skip headers or malformed rows

        status = cols[1].get_text(strip=True)
        ability_name = cols[2].get_text(strip=True)
        ttp = cols[3].get_text(strip=True)

        cmd_pre = row.select_one("div.dropdown-menu .dropdown-content .box pre")
        command = cmd_pre.get_text("\n", strip=True) if cmd_pre else ""

        standard_output = _label_box_text(row, "Standard Output")
        error_text = _label_box_text(row, "Standard Error")

        results.append({
            "Ability Name": ability_name,
            "TTP": ttp,
            "Status": status,
            "Command": command,
            "Output": standard_output,
            "Error": error_text,
        })

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a Caldera HTML report into an Excel sheet.")
    parser.add_argument("html_path", type=Path, help="Path to the report .html file")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output .xlsx path (default: alongside input)")
    args = parser.parse_args()

    output_path = args.output or args.html_path.with_suffix(".xlsx")
    rows = extract_rows(args.html_path)
    if not rows:
        print("No rows extracted from HTML.")
        return
    save_to_excel(rows, output_path, headers=HEADERS)


if __name__ == "__main__":
    main()
