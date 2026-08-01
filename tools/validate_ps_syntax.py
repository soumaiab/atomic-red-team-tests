#!/usr/bin/env python3
"""Syntax-check every 'psh' executor command/cleanup string in generated Caldera
ability/adversary YAML, using PowerShell's own parser (no execution involved).

Catches malformed one-liners produced by generate_caldera_abilities.py's line-
flattening step - e.g. a stray ';' inserted inside a still-open '(' grouping,
which PowerShell rejects at parse time but nothing short of a real parser catches.

Only 'psh' snippets are checked: there's no equivalent no-execute syntax-check API
for cmd.exe batch scripts, so 'cmd' executor commands are left alone.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def extract_psh_snippets(scan_dir: Path) -> list:
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
                if ex.get("name") != "psh":
                    continue
                if ex.get("command"):
                    snippets.append({"file": str(f), "field": "command", "text": ex["command"]})
                for c in ex.get("cleanup", []) or []:
                    snippets.append({"file": str(f), "field": "cleanup", "text": c})
    return snippets


# Snippets are piped over stdin rather than written to a temp file: a file full of
# concatenated ART one-liners (Mimikatz stagers etc.) reliably gets blocked by local
# real-time AV as "contains a virus" the moment anything tries to read it back.
VALIDATOR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$raw = [Console]::In.ReadToEnd()
$snippets = $raw | ConvertFrom-Json
$failures = @()
foreach ($s in $snippets) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput($s.text, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        $failures += [PSCustomObject]@{
            file   = $s.file
            field  = $s.field
            errors = ($parseErrors | ForEach-Object { $_.Message }) -join ' | '
        }
    }
}
[Console]::Error.WriteLine("checked $($snippets.Count) snippets, $($failures.Count) failed to parse")
$failures | ConvertTo-Json -Depth 5
""".strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir", default=str(REPO_ROOT / "caldera-abilities"), help="Directory to scan for ability/adversary YAML"
    )
    args = parser.parse_args()

    scan_dir = Path(args.dir).resolve()
    snippets = extract_psh_snippets(scan_dir)
    if not snippets:
        print(f"No 'psh' command/cleanup snippets found under {scan_dir}")
        return

    encoded = base64.b64encode(VALIDATOR_SCRIPT.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        input=json.dumps(snippets),
        capture_output=True,
        text=True,
    )
    print(proc.stderr.strip())  # the "checked N snippets..." status line
    if proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        sys.exit(1)

    raw = proc.stdout.strip()
    failures = json.loads(raw) if raw else []

    if isinstance(failures, dict):  # PowerShell emits a bare object, not an array, for exactly one failure
        failures = [failures]

    if failures:
        print(f"\n{len(failures)} snippet(s) failed to parse:\n")
        for f in failures:
            print(f"- {f['file']} ({f['field']}): {f['errors']}")
        sys.exit(1)

    print("All psh snippets parsed cleanly.")


if __name__ == "__main__":
    main()
