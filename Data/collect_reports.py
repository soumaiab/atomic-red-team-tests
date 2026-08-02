import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from extract_html import extract_rows
from parse_collection_steps import extract_steps, save_to_excel

MERGED_HEADERS = ["Ability Name", "TTP", "Status", "Command", "Output", "Error"]
PLACEHOLDER_OUTPUTS = {"", "-", "nothing to show"}
ANALYSIS_FILENAME = "analysis-summary.md"


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
            merged.append(row)
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
        return json_rows

    html_rows: list[dict] = []
    for html_file in unit.html_files:
        html_rows.extend(extract_rows(html_file))

    return merge_rows(json_rows, html_rows)


def _tactic_display_name(tactic_dir: Path) -> str:
    name = tactic_dir.name
    return name[: -len("-done")] if name.endswith("-done") else name


def _non_placeholder(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in PLACEHOLDER_OUTPUTS


def _status_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status = (row.get("Status") or "-").strip().lower() or "-"
        counts[status] += 1
    return counts


def count_opensearch_logs(tactic_dir: Path) -> int:
    """Sum the hit count of every OpenSearch export in a tactic folder - files
    named like '*os-logs*.json' or (stealth-done's paginated exports) 'page*.json'."""
    log_files = {*tactic_dir.rglob("*os-logs*.json"), *tactic_dir.rglob("page*.json")}
    total = 0
    for log_file in log_files:
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            total += len(data.get("hits", {}).get("hits", []))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[!] Could not read OpenSearch log file {log_file}: {e}")
    return total


def generate_analysis_md(units_by_tactic: dict[Path, list[ReportUnit]], rows_by_tactic: dict[Path, list[dict]], root: Path) -> str:
    all_rows = [row for rows in rows_by_tactic.values() for row in rows]
    total_reports = sum(len(units) for units in units_by_tactic.values())
    all_ttps = {row["TTP"] for row in all_rows if row.get("TTP") not in (None, "-", "")}
    all_abilities = {row["Ability Name"] for row in all_rows if row.get("Ability Name") not in (None, "-", "")}
    overall_status = _status_counts(all_rows)
    opensearch_logs_by_tactic = {tactic_dir: count_opensearch_logs(tactic_dir) for tactic_dir in rows_by_tactic}
    total_opensearch_logs = sum(opensearch_logs_by_tactic.values())

    lines: list[str] = []
    lines.append("# Data Analysis Summary")
    lines.append("")
    lines.append(f"_Generated by `collect_reports.py` from `{root}` on {datetime.now():%Y-%m-%d %H:%M}_")
    lines.append("")

    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Tactics covered:** {len(rows_by_tactic)}")
    lines.append(f"- **Total reports/runs:** {total_reports}")
    lines.append(f"- **Total logged steps:** {len(all_rows)}")
    lines.append(f"- **Total unique TTPs:** {len(all_ttps)}")
    lines.append(f"- **Total unique abilities tested:** {len(all_abilities)}")
    lines.append(f"- **Total OpenSearch log entries collected:** {total_opensearch_logs:,}")
    if all_rows:
        success = overall_status.get("success", 0)
        lines.append(f"- **Overall success rate:** {success}/{len(all_rows)} ({success / len(all_rows) * 100:.1f}%)")
    lines.append("")

    lines.append("## Per-Tactic Breakdown")
    lines.append("")
    lines.append("| Tactic | Reports | Steps | OpenSearch Logs | Unique TTPs | Unique Abilities | Success | Failure | Timeout | Other |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for tactic_dir in sorted(rows_by_tactic, key=_tactic_display_name):
        rows = rows_by_tactic[tactic_dir]
        ttps = {r["TTP"] for r in rows if r.get("TTP") not in (None, "-", "")}
        abilities = {r["Ability Name"] for r in rows if r.get("Ability Name") not in (None, "-", "")}
        sc = _status_counts(rows)
        other = len(rows) - sc.get("success", 0) - sc.get("failure", 0) - sc.get("timeout", 0)
        lines.append(
            f"| {_tactic_display_name(tactic_dir)} | {len(units_by_tactic[tactic_dir])} | {len(rows)} | "
            f"{opensearch_logs_by_tactic[tactic_dir]:,} | {len(ttps)} | {len(abilities)} | "
            f"{sc.get('success', 0)} | {sc.get('failure', 0)} | {sc.get('timeout', 0)} | {other} |"
        )
    lines.append("")

    lines.append("## Data Completeness")
    lines.append("")
    lines.append("How often a real Standard Output/Error was actually captured, vs. left as a placeholder:")
    lines.append("")
    if all_rows:
        with_output = sum(1 for r in all_rows if _non_placeholder(r.get("Output")))
        with_error = sum(1 for r in all_rows if _non_placeholder(r.get("Error")))
        lines.append(f"- Steps with captured Standard Output: {with_output}/{len(all_rows)} ({with_output / len(all_rows) * 100:.1f}%)")
        lines.append(f"- Steps with captured Standard Error: {with_error}/{len(all_rows)} ({with_error / len(all_rows) * 100:.1f}%)")
    lines.append("")

    ttp_rows: dict[str, list[dict]] = defaultdict(list)
    ttp_tactics: dict[str, set[str]] = defaultdict(set)
    for tactic_dir, rows in rows_by_tactic.items():
        for row in rows:
            ttp = row.get("TTP")
            if ttp in (None, "-", ""):
                continue
            ttp_rows[ttp].append(row)
            ttp_tactics[ttp].add(_tactic_display_name(tactic_dir))

    def procedure_count(ttp: str) -> int:
        return len({(r.get("Command") or "").strip() for r in ttp_rows[ttp]})

    lines.append("## Most-Tested TTPs")
    lines.append("")
    lines.append("Techniques exercised with the most distinct procedures (unique commands) in this dataset:")
    lines.append("")
    top_ttps = sorted(ttp_rows, key=lambda t: (-procedure_count(t), t))[:10]
    for ttp in top_ttps:
        count = procedure_count(ttp)
        if count <= 1:
            break
        tactics = ", ".join(sorted(ttp_tactics[ttp]))
        lines.append(f"- **{ttp}** ({tactics}) — {count} distinct procedures")
    lines.append("")

    lines.append("## Procedures per TTP")
    lines.append("")
    lines.append('"Procedures" = distinct commands observed for that technique ID - i.e. how many different '
                  "ways this repo exercises that technique. Sorted by procedure count, most-tested first.")
    lines.append("")
    lines.append("| TTP | Tactic(s) | Procedures (unique commands) |")
    lines.append("|---|---|---|")
    for ttp in sorted(ttp_rows, key=lambda t: (-procedure_count(t), t)):
        tactics = ", ".join(sorted(ttp_tactics[ttp]))
        lines.append(f"| {ttp} | {tactics} | {procedure_count(ttp)} |")
    lines.append("")

    return "\n".join(lines)


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

    rows_by_tactic: dict[Path, list[dict]] = {}

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
        rows_by_tactic[tactic_dir] = rows

    if args.dry_run:
        print(f"[analysis] would write {root / ANALYSIS_FILENAME}")
        return

    if rows_by_tactic:
        analysis_path = root / ANALYSIS_FILENAME
        analysis_path.write_text(generate_analysis_md(units_by_tactic, rows_by_tactic, root), encoding="utf-8")
        print(f"[ok] Saved to: {analysis_path}")


if __name__ == "__main__":
    main()
