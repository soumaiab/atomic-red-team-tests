#!/usr/bin/env python3
"""Heuristic sanity-check for every 'cmd' executor command/cleanup string in generated
Caldera ability/adversary YAML.

Unlike validate_ps_syntax.py, this is NOT backed by a real parser - cmd.exe has no
equivalent to PowerShell's Language.Parser (no dry-run flag, no exposed parse phase),
and there's no maintained third-party grammar for batch syntax to lean on either. These
are plain structural checks (balance/dangling-operator patterns) that catch the same
class of bug the flattening step could introduce, but passing all of them is not proof
the command is valid cmd.exe syntax - only that it isn't obviously broken.

goto/labels and caret line-continuation are the two cmd constructs that truly cannot
survive being flattened to one line (labels are physical-line jump targets); a scan of
the ART corpus at the time this was written found zero uses of either across all
Windows command_prompt-executor tests, so they aren't checked for here, just noted.
"""
from __future__ import annotations

import argparse
import glob as globmod
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def extract_cmd_snippets(scan_dir: Path) -> list:
    snippets = []
    for f in sorted(scan_dir.glob("**/*.yml")):
        with open(f, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        if not doc:
            continue
        abilities = doc if isinstance(doc, list) else [doc]
        for a in abilities:
            if not isinstance(a, dict):
                continue
            for ex in a.get("executors", []) or []:
                if ex.get("name") != "cmd":
                    continue
                if ex.get("command"):
                    snippets.append({"file": str(f), "field": "command", "text": ex["command"]})
                for c in ex.get("cleanup", []) or []:
                    snippets.append({"file": str(f), "field": "cleanup", "text": c})
    return snippets


def _balanced_outside_quotes(text: str, open_char: str, close_char: str) -> bool:
    depth = 0
    in_quotes = False
    for c in text:
        if c == '"':
            in_quotes = not in_quotes
        elif not in_quotes:
            if c == open_char:
                depth += 1
            elif c == close_char:
                depth -= 1
                if depth < 0:
                    return False
    return depth == 0


def check_snippet(text: str) -> list:
    issues = []
    if not _balanced_outside_quotes(text, "(", ")"):
        issues.append("unbalanced parentheses")
    if text.count('"') % 2 != 0:
        issues.append("unbalanced double quotes")
    if text.replace("%%", "").count("%") % 2 != 0:
        issues.append("unbalanced '%' variable reference")
    stripped = text.strip()
    if stripped and (stripped[0] in "&|" or stripped[-1] in "&|"):
        issues.append("dangling '&' or '|' at start/end")
    if re.search(r"\(\s*\)", text):
        issues.append("empty '( )' block")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir", default=str(REPO_ROOT / "caldera-abilities"), help="Directory to scan for ability/adversary YAML"
    )
    args = parser.parse_args()

    scan_dir = Path(args.dir).resolve()
    snippets = extract_cmd_snippets(scan_dir)
    if not snippets:
        print(f"No 'cmd' command/cleanup snippets found under {scan_dir}")
        return

    failures = []
    for s in snippets:
        issues = check_snippet(s["text"])
        if issues:
            failures.append({"file": s["file"], "field": s["field"], "issues": issues})

    print(f"checked {len(snippets)} cmd snippets (heuristic checks only), {len(failures)} flagged")

    if failures:
        print(f"\n{len(failures)} snippet(s) flagged:\n")
        for f in failures:
            print(f"- {f['file']} ({f['field']}): {', '.join(f['issues'])}")
        sys.exit(1)

    print("No structural issues found (balance/dangling-operator checks only - not a real syntax guarantee).")


if __name__ == "__main__":
    main()
