import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from extract_html import extract_rows
from parse_collection_steps import extract_steps, save_to_excel

MERGED_HEADERS = ["Ability Name", "TTP", "Status", "Command", "Output", "Error", "Source"]
PLACEHOLDER_OUTPUTS = {"", "-", "nothing to show"}


@dataclass
class ReportUnit:
    report_json: Path
    html_files: list[Path] = field(default_factory=list)


def _report_stem(report_json: Path) -> str:
    stem = report_json.stem
    if stem.endswith("_report"):
        stem = stem[: -len("_report")]
    return stem


def discover_units(root: Path) -> list[ReportUnit]:
    units: list[ReportUnit] = []
    by_dir: dict[Path, list[Path]] = {}
    for report_json in sorted(root.rglob("*_report.json")):
        by_dir.setdefault(report_json.parent, []).append(report_json)

    for directory, reports in by_dir.items():
        html_files = sorted(directory.glob("*.html"))

        if not html_files:
            for report_json in reports:
                units.append(ReportUnit(report_json, []))
            continue

        if len(reports) == 1:
            units.append(ReportUnit(reports[0], html_files))
            continue

        # Multiple reports sharing a directory that also has HTML: only pair
        # HTML with a report when the filenames clearly correspond, so one
        # report's HTML is never merged into another report's rows.
        unclaimed_html = set(html_files)
        for report_json in reports:
            stem = _report_stem(report_json)
            matched = [
                html for html in html_files
                if stem in html.stem or html.stem in stem
            ]
            for html in matched:
                unclaimed_html.discard(html)
            if not matched:
                print(f"[!] {report_json.name}: could not confidently match any HTML in "
                      f"{directory} - treating as JSON-only.")
            units.append(ReportUnit(report_json, matched))

        if unclaimed_html:
            names = ", ".join(sorted(h.name for h in unclaimed_html))
            print(f"[!] {directory}: HTML file(s) not matched to any report: {names}")

    return units


def build_merge_key(ability_name: str, command: str) -> tuple[str, str]:
    return (ability_name or "").strip(), " ".join((command or "").split())


def merge_rows(json_rows: list[dict], html_rows: list[dict]) -> list[dict]:
    html_index: dict[tuple[str, str], list[dict]] = {}
    for row in html_rows:
        key = build_merge_key(row["Ability Name"], row["Command"])
        html_index.setdefault(key, []).append(row)

    merged: list[dict] = []
    for row in json_rows:
        key = build_merge_key(row["Ability Name"], row["Command"])
        bucket = html_index.get(key)
        html_row = bucket.pop(0) if bucket else None

        if html_row is None:
            merged.append({**row, "Source": "json"})
            continue

        def pick(field_name: str) -> str:
            html_value = (html_row.get(field_name) or "").strip()
            if html_value and html_value.lower() not in PLACEHOLDER_OUTPUTS:
                return html_value
            return row.get(field_name, "-")

        merged.append({
            "Ability Name": row["Ability Name"],
            "TTP": row["TTP"],
            "Status": row["Status"],
            "Command": row["Command"],
            "Output": pick("Output"),
            "Error": pick("Error"),
            "Source": "both",
        })

    for bucket in html_index.values():
        for html_row in bucket:
            merged.append({
                "Ability Name": html_row["Ability Name"],
                "TTP": "-",
                "Status": html_row["Status"],
                "Command": html_row["Command"],
                "Output": html_row["Output"],
                "Error": html_row["Error"],
                "Source": "html",
            })

    return merged


def find_tactic_dir(directory: Path, root: Path) -> Path:
    """Walk up from a report's directory to its tactic-level ancestor (every
    tactic folder in this repo is named '<tactic>-done'), bounded by root."""
    current = directory
    while True:
        if current.name.endswith("-done"):
            return current
        if current == root or current.parent == current:
            return directory  # no '-done' ancestor within the scanned tree
        current = current.parent


def tactic_output_path(tactic_dir: Path, output_dir: Path | None, root: Path) -> Path:
    filename = f"{tactic_dir.name}.xlsx"
    if output_dir is None:
        return tactic_dir / filename
    rel_dir = tactic_dir.relative_to(root) if tactic_dir != root else Path(".")
    return output_dir / rel_dir / filename


def extract_unit_rows(unit: ReportUnit) -> list[dict]:
    with open(unit.report_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    json_rows = extract_steps(data)

    if not unit.html_files:
        return [{**row, "Source": "json"} for row in json_rows]

    html_rows: list[dict] = []
    for html_file in unit.html_files:
        html_rows.extend(extract_rows(html_file))

    return merge_rows(json_rows, html_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk a directory tree of Caldera report.json / HTML report pairs and "
                     "export one merged Excel workbook per tactic folder."
    )
    parser.add_argument("path", nargs="?", type=Path, default=Path("Data"),
                         help="File tree to scan (default: Data)")
    parser.add_argument("--output-dir", type=Path, default=None,
                         help="Write outputs under this directory instead of at each tactic folder's root")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print discovered report/HTML pairings and planned output paths without writing")
    args = parser.parse_args()

    root = args.path
    units = discover_units(root)

    if not units:
        print(f"No *_report.json files found under {root}")
        return

    units_by_tactic: dict[Path, list[ReportUnit]] = {}
    for unit in units:
        tactic_dir = find_tactic_dir(unit.report_json.parent, root)
        units_by_tactic.setdefault(tactic_dir, []).append(unit)

    for tactic_dir, tactic_units in units_by_tactic.items():
        merged_path = tactic_output_path(tactic_dir, args.output_dir, root)

        if args.dry_run:
            for unit in tactic_units:
                html_names = [h.name for h in unit.html_files] or "none"
                print(f"{unit.report_json} + html={html_names}")
            print(f"[tactic] {tactic_dir} ({len(tactic_units)} report(s)) -> {merged_path}")
            continue

        rows: list[dict] = []
        for unit in tactic_units:
            rows.extend(extract_unit_rows(unit))

        if not rows:
            print(f"[!] No valid steps found under {tactic_dir}")
            continue

        merged_path.parent.mkdir(parents=True, exist_ok=True)
        save_to_excel(rows, merged_path, headers=MERGED_HEADERS)


if __name__ == "__main__":
    main()
