#!/usr/bin/env python3
"""Convert Atomic Red Team atomic tests into Caldera ability YAML files.

See docs/caldera-ability-generation-plan.md for the full design.
"""
from __future__ import annotations

import argparse
import base64
import csv
import re
import shutil
import sys
import uuid
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ATOMICS_ROOT = REPO_ROOT / "atomic-red-team" / "atomics"
WINDOWS_INDEX_CSV = ATOMICS_ROOT / "Indexes" / "Indexes-CSV" / "windows-index.csv"

# Fixed namespace so regenerating the abilities produces the same new IDs each run
# (re-importing into Caldera updates existing abilities instead of duplicating them).
# IDs are derived from name+original-ART-guid rather than the ART guid alone, so they
# don't collide with any pre-existing Caldera ability (e.g. Stockpile's own ART-derived
# abilities, which commonly reuse ART's raw guid as their own ability id).
ABILITY_ID_NAMESPACE = uuid.UUID("f6a1b2c3-7d4e-4a1a-9c3b-2e5d6f7a8b9c")
NAME_PREFIX = "Soumaia ART Tests - "

EXECUTOR_TO_SHELL = {"powershell": "psh", "command_prompt": "cmd"}
DEFAULT_TIMEOUT = 60

PLACEHOLDER_RE = re.compile(r"#\{([A-Za-z0-9_]+)\}")
PATH_TO_ATOMICS_RE = re.compile(r"PathToAtomicsFolder[\\/]([^\s\"'`)]+)")

# Keywords that must stay adjacent to the previous statement's closing '}' with only
# whitespace between them (PowerShell's try/catch/finally and if/elseif/else grammar
# doesn't allow a ';' statement-separator there, only a 'soft' newline/whitespace break).
PS_CONTINUATION_KEYWORDS = ("}", "catch", "finally", "else")


class Report:
    def __init__(self):
        self.total_seen = 0
        self.generated = 0
        self.skipped = {}  # reason -> count
        self.unmapped = []  # (tech_id, guid, name)
        self.manual_input = []  # (tech_id, guid, name, [arg names])
        self.warnings = []  # free-text warnings
        self.payloads_copied = []  # filenames
        self.payloads_missing = []  # (tech_id, guid, path)
        self.missing_payload_abilities = []  # (tech_id, guid, name) routed to _missing-payloads/
        self.cmd_dependency_notes = []  # (tech_id, guid) needing manual review

    def skip(self, reason):
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def render(self) -> str:
        lines = []
        lines.append("# Caldera ability generation report\n")
        lines.append(f"- Atomic tests scanned: {self.total_seen}")
        lines.append(f"- Abilities generated: {self.generated}")
        lines.append(f"- Unmapped tactic (routed to `_unmapped/`): {len(self.unmapped)}")
        lines.append(f"- Payload files copied: {len(self.payloads_copied)}")
        lines.append(f"- Payload source files missing on disk: {len(self.payloads_missing)}")
        lines.append(f"- Abilities with a missing payload (routed to `_missing-payloads/`): {len(self.missing_payload_abilities)}")
        lines.append(f"- Tests needing manual input (no default for an argument): {len(self.manual_input)}")
        lines.append(f"- cmd-shell dependency prereqs (best-effort escaping, recommend review): {len(self.cmd_dependency_notes)}")
        lines.append("")
        lines.append("## Skipped tests by reason")
        if not self.skipped:
            lines.append("- (none)")
        for reason, count in sorted(self.skipped.items()):
            lines.append(f"- {reason}: {count}")
        lines.append("")
        lines.append("## Unmapped tactic")
        if not self.unmapped:
            lines.append("- (none)")
        for tech_id, guid, name in self.unmapped:
            lines.append(f"- {tech_id} / {guid} / {name}")
        lines.append("")
        lines.append("## Tests needing manual input (placeholder left unresolved)")
        if not self.manual_input:
            lines.append("- (none)")
        for tech_id, guid, name, args in self.manual_input:
            lines.append(f"- {tech_id} / {guid} / {name}: {', '.join(args)}")
        lines.append("")
        lines.append("## Payload source files missing on disk")
        if not self.payloads_missing:
            lines.append("- (none)")
        for tech_id, guid, path in self.payloads_missing:
            lines.append(f"- {tech_id} / {guid}: {path}")
        lines.append("")
        lines.append("## Abilities with a missing payload (routed to `_missing-payloads/`)")
        if not self.missing_payload_abilities:
            lines.append("- (none)")
        for tech_id, guid, name in self.missing_payload_abilities:
            lines.append(f"- {tech_id} / {guid} / {name}")
        lines.append("")
        lines.append("## cmd-shell dependency prereqs (manual review recommended)")
        if not self.cmd_dependency_notes:
            lines.append("- (none)")
        for tech_id, guid in self.cmd_dependency_notes:
            lines.append(f"- {tech_id} / {guid}")
        lines.append("")
        lines.append("## Warnings")
        if not self.warnings:
            lines.append("- (none)")
        for w in self.warnings:
            lines.append(f"- {w}")
        lines.append("")
        return "\n".join(lines)


def load_tactic_manifest(csv_path: Path, report: Report) -> dict:
    manifest = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key = (row["Technique #"].strip(), row["Test GUID"].strip())
            tactic = row["Tactic"].strip().lower()
            if key in manifest:
                if manifest[key] != tactic:
                    report.warnings.append(
                        f"ambiguous tactic for {key}: keeping '{manifest[key]}', also saw '{tactic}'"
                    )
                continue
            manifest[key] = tactic
    return manifest


def resolve_path_to_atomics(
    text: str,
    shell: str,
    payloads_dir: Path,
    copied_payloads: dict,
    payload_names: set,
    tech_id: str,
    guid: str,
    report: Report,
    dry_run: bool,
    missing_this_test: list,
) -> str:
    if not text or "PathToAtomicsFolder" not in text:
        return text

    def repl(m: re.Match) -> str:
        rest = m.group(1)
        norm = rest.replace("\\", "/")
        lower = norm.lower()
        for prefix in ("../externalpayloads", "externalpayloads"):
            if lower.startswith(prefix):
                tail = norm[len(prefix):].lstrip("/")
                scratch = "$env:TEMP\\ART-ExternalPayloads" if shell == "psh" else "%TEMP%\\ART-ExternalPayloads"
                if tail:
                    return scratch + "\\" + tail.replace("/", "\\")
                return scratch

        # Vendored file committed in the ART repo (atomics/<tech>/src|bin/...)
        filename = norm.rstrip("/").split("/")[-1]
        src_path = (ATOMICS_ROOT / norm).resolve()
        if src_path.is_file():
            if filename in copied_payloads and copied_payloads[filename] != src_path:
                report.warnings.append(
                    f"payload filename collision: '{filename}' referenced from both "
                    f"{copied_payloads[filename]} and {src_path}"
                )
            elif filename not in copied_payloads:
                if not dry_run:
                    payloads_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src_path, payloads_dir / filename)
                copied_payloads[filename] = src_path
                report.payloads_copied.append(filename)
        else:
            report.payloads_missing.append((tech_id, guid, str(norm)))
            missing_this_test.append(str(norm))
        payload_names.add(filename)
        return f".\\{filename}"

    return PATH_TO_ATOMICS_RE.sub(repl, text)


def substitute_args(text: str, resolved_args: dict, missing: list) -> str:
    if not text:
        return text

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in resolved_args:
            return resolved_args[name]
        missing.append(name)
        return m.group(0)

    return PLACEHOLDER_RE.sub(repl, text)


def strip_ps_line_comment(line: str) -> str:
    """Drop a trailing '# ...' PowerShell comment, tracking quotes so a '#' inside a
    string literal (e.g. a URL fragment) isn't mistaken for a comment marker."""
    in_single = False
    in_double = False
    for i, c in enumerate(line):
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double:
            return line[:i].rstrip()
    return line.rstrip()


def flatten_psh(script: str) -> str:
    """Collapse a multi-line PowerShell script into one logical line (';'-joined),
    dropping comment lines and respecting try/catch/if/else adjacency rules."""
    lines = []
    for raw in script.splitlines():
        stripped = strip_ps_line_comment(raw.strip())
        if stripped:
            lines.append(stripped)
    if not lines:
        return ""
    parts = [lines[0]]
    for i in range(1, len(lines)):
        prev, cur = lines[i - 1], lines[i]
        if prev.endswith(("{", ";")) or cur.lower().startswith(PS_CONTINUATION_KEYWORDS):
            parts.append(" " + cur)
        else:
            parts.append("; " + cur)
    return "".join(parts)


def flatten_cmd(script: str) -> str:
    """Collapse a multi-line batch script into one logical line ('&'-joined), dropping
    comment lines (REM / ::)."""
    lines = []
    for raw in script.splitlines():
        s = raw.strip()
        if not s or s.startswith("::") or s.lower() == "rem" or s.lower().startswith("rem "):
            continue
        lines.append(s)
    return " & ".join(lines)


def flatten_script(script: str, shell: str) -> str:
    if not script:
        return ""
    return flatten_psh(script) if shell == "psh" else flatten_cmd(script)


def encode_ps_command(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def shell_invocation(script: str, shell: str) -> str:
    """One-line invocation of `script` (written for `shell`) as an isolated subprocess,
    so its `exit 0/1` only signals pass/fail instead of killing the ability's own process."""
    if shell == "psh":
        return f"powershell -NoProfile -EncodedCommand {encode_ps_command(script)}"
    escaped = flatten_cmd(script).replace('"', '""')
    return f'cmd /c "{escaped}"'


def dependency_block(prereq: str, get_prereq: str, dep_shell: str, main_shell: str) -> str:
    prereq_inv = shell_invocation(prereq, dep_shell)
    get_inv = shell_invocation(get_prereq, dep_shell)
    if main_shell == "psh":
        return f"{prereq_inv}; if ($LASTEXITCODE -ne 0) {{ {get_inv} }}"
    return f'{prereq_inv} & if not "%ERRORLEVEL%"=="0" ( {get_inv} )'


def process_test(
    test: dict,
    tech_id: str,
    display_name: str,
    tactic_manifest: dict,
    payloads_dir: Path,
    copied_payloads: dict,
    report: Report,
    dry_run: bool,
    timeout: int,
):
    report.total_seen += 1
    guid = test.get("auto_generated_guid")
    name = test.get("name", "")

    if "windows" not in (test.get("supported_platforms") or []):
        report.skip("not supported on windows")
        return None

    executor = test.get("executor") or {}
    ex_name = executor.get("name")
    if ex_name not in EXECUTOR_TO_SHELL:
        report.skip(f"unsupported executor: {ex_name}")
        return None
    shell = EXECUTOR_TO_SHELL[ex_name]

    key = (tech_id, guid)
    tactic = tactic_manifest.get(key)
    unmapped = tactic is None

    payload_names: set = set()
    missing_arg_refs: list = []
    missing_payloads_this_test: list = []

    def resolve_pta(text, ctx_shell):
        return resolve_path_to_atomics(
            text, ctx_shell, payloads_dir, copied_payloads, payload_names, tech_id, guid, report, dry_run,
            missing_payloads_this_test,
        )

    input_arguments = test.get("input_arguments") or {}
    no_default_args = []

    def build_resolved_args(ctx_shell):
        resolved = {}
        for arg_name, spec in input_arguments.items():
            if isinstance(spec, dict) and spec.get("default") is not None:
                value = resolve_pta(str(spec["default"]), ctx_shell)
                # Argument values get substituted inline into an already-flattened
                # single-line command, so any embedded newline in a default has to go.
                resolved[arg_name] = " ".join(value.split())
            else:
                no_default_args.append(arg_name)
        return resolved

    resolved_args_main = build_resolved_args(shell)

    dependencies = test.get("dependencies") or []
    dep_shell = None
    resolved_args_dep = resolved_args_main
    if dependencies:
        dep_exec_name = test.get("dependency_executor_name", ex_name)
        dep_shell = EXECUTOR_TO_SHELL.get(dep_exec_name, shell)
        resolved_args_dep = build_resolved_args(dep_shell)

    def resolve(text, ctx_shell, args_map):
        if not text:
            return ""
        t = resolve_pta(text, ctx_shell)
        return substitute_args(t, args_map, missing_arg_refs)

    main_command = flatten_script(resolve(executor.get("command", ""), shell, resolved_args_main), shell)
    cleanup_raw = executor.get("cleanup_command")
    cleanup_resolved = (
        flatten_script(resolve(cleanup_raw, shell, resolved_args_main), shell) if cleanup_raw else None
    )

    preamble_blocks = []
    for dep in dependencies:
        prereq = resolve(dep.get("prereq_command", ""), dep_shell, resolved_args_dep)
        get_prereq = resolve(dep.get("get_prereq_command", ""), dep_shell, resolved_args_dep)
        preamble_blocks.append(dependency_block(prereq, get_prereq, dep_shell, shell))
    if dep_shell == "cmd":
        report.cmd_dependency_notes.append((tech_id, guid))

    joiner = "; " if shell == "psh" else " & "
    full_command = joiner.join(preamble_blocks + [main_command]) if preamble_blocks else main_command

    if no_default_args or missing_arg_refs:
        report.manual_input.append((tech_id, guid, name, sorted(set(no_default_args) | set(missing_arg_refs))))

    description = (test.get("description") or "").strip()
    if executor.get("elevation_required"):
        description = f"{description} (requires elevation)"

    new_id = str(uuid.uuid5(ABILITY_ID_NAMESPACE, f"{name}:{guid}"))

    ability = {
        "requirements": [],
        "name": f"{NAME_PREFIX}{name}",
        "description": description,
        "tactic": tactic or "",
        "technique_id": tech_id,
        "technique_name": display_name,
        "executors": [
            {
                "cleanup": [cleanup_resolved] if cleanup_resolved else [],
                "timeout": timeout,
                "platform": "windows",
                "name": shell,
                "payloads": sorted(payload_names),
                "parsers": [],
                "command": full_command,
            }
        ],
        "id": new_id,
    }

    if unmapped:
        report.unmapped.append((tech_id, guid, name))

    has_missing_payload = bool(missing_payloads_this_test)
    if has_missing_payload:
        report.missing_payload_abilities.append((tech_id, guid, name))

    report.generated += 1
    return ability, unmapped, has_missing_payload


def write_ability(ability: dict, unmapped: bool, has_missing_payload: bool, out_dir: Path, dry_run: bool):
    guid = ability["id"]
    if unmapped:
        dest = out_dir / "_unmapped" / f"{guid}.yml"
    elif has_missing_payload:
        dest = out_dir / "_missing-payloads" / ability["tactic"] / f"{guid}.yml"
    else:
        dest = out_dir / "abilities" / ability["tactic"] / f"{guid}.yml"

    text = yaml.dump(
        [ability],
        Dumper=yaml.SafeDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=1_000_000,  # never wrap long single-line commands (e.g. base64 blobs) across lines
    )

    if dry_run:
        print(f"--- {dest.relative_to(out_dir.parent) if dest.is_relative_to(out_dir.parent) else dest} ---")
        print(text)
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--technique", help="Restrict to a single technique folder, e.g. T1059.001")
    parser.add_argument("--dry-run", action="store_true", help="Parse + resolve + print, don't write files")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "caldera-abilities"), help="Output directory")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"Executor timeout in seconds (default {DEFAULT_TIMEOUT})"
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    payloads_dir = out_dir / "payloads"

    report = Report()
    tactic_manifest = load_tactic_manifest(WINDOWS_INDEX_CSV, report)
    copied_payloads: dict = {}

    tech_dirs = sorted(ATOMICS_ROOT.glob("T*"))
    if args.technique:
        tech_dirs = [d for d in tech_dirs if d.name == args.technique]
        if not tech_dirs:
            print(f"No technique folder found for {args.technique}", file=sys.stderr)
            sys.exit(1)

    for tech_dir in tech_dirs:
        yaml_file = tech_dir / f"{tech_dir.name}.yaml"
        if not yaml_file.is_file():
            continue
        try:
            with open(yaml_file, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError as e:
            report.warnings.append(f"failed to parse {yaml_file}: {e}")
            continue
        if not doc or "atomic_tests" not in doc:
            continue

        tech_id = doc.get("attack_technique", tech_dir.name)
        display_name = doc.get("display_name", tech_id)

        for test in doc["atomic_tests"]:
            result = process_test(
                test, tech_id, display_name, tactic_manifest, payloads_dir, copied_payloads, report, args.dry_run,
                args.timeout,
            )
            if result is None:
                continue
            ability, unmapped, has_missing_payload = result
            write_ability(ability, unmapped, has_missing_payload, out_dir, args.dry_run)

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "generation-report.md").write_text(report.render(), encoding="utf-8")
        print(f"Generated {report.generated} abilities into {out_dir}")
        print(f"See {out_dir / 'generation-report.md'} for details")
    else:
        print(report.render())


if __name__ == "__main__":
    main()
