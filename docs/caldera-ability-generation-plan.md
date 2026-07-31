# Convert Atomic Red Team tests into Caldera abilities

## Context

`atomic-red-team/` is now cloned into this repo as a submodule. Its ~336 `atomics/T*/T*.yaml` files each define one or more "atomic tests" per ATT&CK technique, but they aren't directly runnable — commands use `#{argument}` placeholders resolved from `input_arguments` defaults, a `PathToAtomicsFolder` literal placeholder, and sometimes a `dependencies` block that must fetch a tool before the main command works. The user has a working Caldera instance and wants these turned into artifacts Caldera can execute directly, so they can run ART's technique library as Caldera operations instead of by hand.

Confirmed against a real example fetched from `mitre/stockpile` (`data/abilities/execution/315cedf1-....yml`), Caldera's native ability schema is:

```yaml
- id: <uuid>
  name: <string>
  description: <string>
  tactic: <string>
  technique:
    attack_id: <T####[.###]>
    name: <string>
  platforms:
    windows:
      psh:            # or cmd, or "psh,pwsh"
        command: |
          <script>
        cleanup: |
          <script>
        payloads:
        - <filename>   # files Caldera stages into the working dir at runtime
```

Generating this format directly (rather than plain `.ps1` + a metadata sidecar) means the output can be dropped straight into a Caldera abilities directory or pasted into Caldera's "create ability" UI — no manual reassembly step for the user.

**Decisions locked in with the user:**
- Extract both `executor: powershell` (~690 tests) and `executor: command_prompt` (~551 tests), mapped natively to Caldera's `psh` and `cmd` executors respectively — no fragile unwrapping of nested `powershell.exe` calls inside cmd strings.
- Output format: Caldera ability YAML (one file per test).
- Dependencies (`get_prereq_command`) are inlined into the generated command so the ability is self-contained at runtime.
- Scope: all Windows-supported techniques in the submodule (full run, not a subset).

## Source-of-truth files

- `atomic-red-team/atomics/T*/T*.yaml` — the test definitions (command, cleanup, input_arguments, dependencies).
- `atomic-red-team/atomics/Indexes/Indexes-CSV/windows-index.csv` — flat `Tactic,Technique #,Technique Name,Test #,Test Name,Test GUID,Executor Name` rows. This is the join key for `tactic`, since the raw test YAML doesn't carry tactic itself. Join on `(Technique #, Test GUID)`.

## Build one Python script: `tools/generate_caldera_abilities.py`

Python 3.13 + PyYAML are already available in this environment — no new dependencies needed.

### 1. Load the tactic manifest
Parse `windows-index.csv` into a dict keyed by `(technique_id, test_guid) -> tactic`. If a technique/guid pair appears under more than one tactic, take the first and log a warning (don't silently duplicate the ability).

### 2. Walk and parse
`glob("atomic-red-team/atomics/T*/T*.yaml")`, `yaml.safe_load` each file. For each `atomic_tests[]` entry:
- Skip if `"windows" not in supported_platforms`.
- Skip if `executor.name not in {"powershell", "command_prompt"}` (covers `manual`, `sh`, `bash`).
- Look up tactic via the manifest using `auto_generated_guid`; if missing, route the ability into a separate `_unmapped/` output folder instead of dropping it, and log a warning.

### 3. Resolve placeholders (this is the core logic — get it right)
Build `resolved_args = {name: str(input_arguments[name]["default"])}` for the test (skip/flag args with no default — leave `#{name}` intact and note it in the report, since it genuinely requires user input).

For every raw string that can contain placeholders (`input_arguments` defaults themselves, `dependencies[].prereq_command`, `dependencies[].get_prereq_command`, `executor.command`, `executor.cleanup_command`), apply substitution in this order:

1. **`PathToAtomicsFolder` — two distinct cases, don't conflate them:**
   - `PathToAtomicsFolder\..\ExternalPayloads\...` (runtime-download cache pattern, e.g. Procdump/Mimikatz/SharpHound) → rewrite to a target-host scratch dir: `$env:TEMP\ART-ExternalPayloads` in psh contexts, `%TEMP%\ART-ExternalPayloads` in cmd contexts. (The analyst's local clone path is meaningless on the Caldera target host — don't substitute the literal local path.)
   - `PathToAtomicsFolder\<TECH_ID>\src\<file>` (reference to a file *committed in the ART repo*, e.g. `T1595.003`'s `wordlist.txt`) → this file must become a Caldera **payload**: copy it into the output's shared `payloads/` folder, add its filename to the ability's `payloads:` list, and rewrite the command's reference to the bare filename (`.\wordlist.txt`), matching how the real stockpile example above references `Emulate-Administrator-Tasks.ps1`.
   - Leave any raw `https://raw.githubusercontent.com/...` URLs baked into `input_arguments` defaults untouched — target host fetches those directly at runtime, same as vanilla ART behavior; don't try to localize these.
2. **`#{arg_name}`** → substitute from `resolved_args`, two passes (a default can itself reference `PathToAtomicsFolder`, already handled by step 1 running first).

### 4. Assemble the final command (handle same-shell vs. cross-shell dependencies)
- No `dependencies`: `command` = resolved `executor.command` verbatim.
- `dependencies` present: for each dependency, wrap `prereq_command` in a **subshell invocation via `-EncodedCommand`** (base64-encoded), not inline in the ability's own session — because ART's `prereq_command` calls `exit 0/1` directly, which would kill the whole ability's process if run inline rather than just signaling pass/fail. This one technique handles both cases uniformly:
  - **Same shell** (`dependency_executor_name == executor.name`): run the prereq check via `powershell -NoProfile -EncodedCommand <b64>`, branch on `$LASTEXITCODE`/`%ERRORLEVEL%`, run `get_prereq_command` (also `-EncodedCommand`) if not satisfied, then the main command.
  - **Cross shell** (e.g. `dependency_executor_name: powershell` but `executor.name: command_prompt`, seen in `T1003.001`'s ProcDump test): from a `cmd` command block, invoke `powershell -NoProfile -EncodedCommand <b64 of prereq/get_prereq>`, check `%ERRORLEVEL%`, then continue with the native cmd main command. Same trick bridges psh-hosting-cmd-prereqs if that direction ever occurs.
- `cleanup_command` (if present) → resolved and placed in the ability's `cleanup:` field, same substitution rules.

### 5. Emit ability YAML
- `id`: reuse `auto_generated_guid` from the ART test (already a stable UUID).
- `name`: `f"{test['name']}"` (technique context already lives in `technique.name`).
- `description`: resolved `description` text; if `elevation_required: true`, append `" (requires elevation)"` — don't invent an unconfirmed Caldera schema field for this, only one field set was verified.
- `tactic`: from the CSV join, lowercased.
- `technique.attack_id` / `technique.name`: from `attack_technique` / `display_name`.
- `platforms.windows.<psh|cmd>.command` / `.cleanup` / `.payloads`: as built above.
- Write one file per test to `caldera-abilities/abilities/<tactic>/<guid>.yml`, mirroring stockpile's own `data/abilities/<tactic>/<guid>.yml` layout — so the output folder can double as a Caldera plugin's `abilities/` directory as-is.

### 6. Output layout
```
caldera-abilities/
  abilities/<tactic>/<guid>.yml     # one ability per test
  payloads/<filename>                # shared flat pool, matches real stockpile convention
  _unmapped/<guid>.yml               # tests whose tactic couldn't be resolved — manual review
  generation-report.md               # totals, skipped tests + reasons, warnings (missing defaults, ambiguous tactic, etc.)
```
Keep this output directory in the parent repo (`atomic-red-team-tests/caldera-abilities/`), not inside the `atomic-red-team` submodule — writing generated content into the submodule would leave it permanently dirty against upstream.

### 7. CLI ergonomics for verification
Give the script flags: `--technique T1059.001` (restrict to one technique folder, for spot-checking), `--dry-run` (parse + resolve + print, don't write files), `--out-dir` (default `caldera-abilities/`). This lets us validate the tricky cases (same-shell dependency, cross-shell dependency, vendored `src/` payload, raw-URL reference, multi-test technique, `cmd`-executor test) individually before the full ~1200-test run.

## Verification plan (no live Caldera access from here)
1. `yaml.safe_load` round-trip every generated file — confirms syntactically valid YAML.
2. Run `--technique` against one example of each tricky pattern found during exploration (`T1059.001` mixed-executor + raw-URL src, `T1003.001` cross-shell dependency, `T1595.003` vendored src payload, `T1547.001` plain psh) and manually diff the generated command against the source YAML to confirm placeholder resolution and shell-bridging logic are correct.
3. Read `generation-report.md` after the full run — check the skip/warning counts are sane (not e.g. 90% unmapped, which would indicate the CSV join key is wrong).
4. Recommend the user hand-import 2-3 generated ability files into their actual Caldera instance as a final acceptance check — that step can't be done from this environment.
