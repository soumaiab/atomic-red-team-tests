# Atomic Red Team → Caldera Abilities

This repo turns [Atomic Red Team](https://github.com/redcanaryco/atomic-red-team) (ART) tests into
[Caldera](https://github.com/mitre/caldera) **abilities**, so you can run ART's library of attack
techniques as Caldera operations instead of copy-pasting commands by hand.

## What's an "Atomic Red Team test" and a "Caldera ability"?

- **Atomic Red Team** is a library of small, self-contained scripts that each simulate one
  technique attackers use (e.g. "dump credentials from memory", "add a registry run key for
  persistence"). Each technique is identified by a [MITRE ATT&CK](https://attack.mitre.org/) ID
  like `T1059.001`. ART stores these as YAML files with a command to run, an optional cleanup
  command, and sometimes a list of arguments you can customize.
- **Caldera** is an attack-emulation platform. You point it at a machine (running a Caldera
  "agent"), and it executes a sequence of **abilities** — also YAML files, but in Caldera's own
  format — against that machine, tracking what ran and what happened.
- The two formats are different enough that you can't just hand Caldera an ART file. This repo's
  job is to translate ~1,800 ART test definitions into ~1,200 ready-to-use Caldera ability files
  (some tests don't apply to Windows, or can't be run automatically — more on that below).

## Repo layout

```
atomic-red-team/        Git submodule — the original ART test library (source data, untouched)
tools/
  generate_caldera_abilities.py   The conversion script (see below)
caldera-abilities/       Generated output (created by running the script — see "Output" below)
docs/
  caldera-ability-generation-plan.md   The original design doc this script was built from
```

`atomic-red-team/` is a **submodule** — a separate git repository embedded inside this one. Don't
hand-edit files inside it; if you need newer ART tests, update the submodule instead.

## How the generator works (in plain terms)

The script `tools/generate_caldera_abilities.py` reads every ART test file under
`atomic-red-team/atomics/T*/T*.yaml` and, for each individual test inside it, does the following:

1. **Filters to what's usable.** Only tests that (a) support Windows and (b) use a PowerShell or
   Command Prompt command are converted. Tests that only have "manual" instructions (read a page
   and click buttons yourself) or only target Linux/macOS are skipped.

2. **Figures out the ATT&CK "tactic".** Caldera groups abilities by tactic (e.g. `persistence`,
   `credential-access`). ART's test files don't actually say which tactic they belong to, so the
   script cross-references a separate index file ART ships (`windows-index.csv`) to look it up.
   If a test isn't listed there, it can't be safely categorized — see the `_unmapped/` output
   folder below.

3. **Fills in the blanks (placeholders).** ART commands use "fill in the blank" style
   placeholders instead of hardcoded values, so a test can be re-used with different inputs. For
   example, a command might say `Invoke-Something -Url #{download_url}`, where `#{download_url}`
   is a placeholder that ART defines a default value for elsewhere in the file. The script
   replaces every placeholder with its default value so the resulting command is ready to run
   as-is, no manual editing needed.

4. **Handles files a test needs (payloads).** Some tests reference a helper file that lives
   inside the ART repo itself (e.g. a wordlist, a small `.exe`, a `.ps1` helper script). The
   script copies these into `caldera-abilities/payloads/` and rewrites the command to reference
   just the filename — that's the same mechanism Caldera itself uses to hand a file to an agent
   at runtime, so the ability is fully self-contained.

   Separately, *some* tests reference a **runtime download cache** — a folder where a tool like
   Mimikatz gets downloaded to and re-used across runs — rather than a file that's actually
   committed in the ART repo. Those references get rewritten to point at a scratch folder on
   whichever machine the ability eventually runs on (`%TEMP%\ART-ExternalPayloads`), since the
   analyst's local folder path is meaningless on a target machine.

5. **Handles "dependencies" (prerequisite checks).** A handful of tests first check something
   (e.g. "is this tool already installed?") and, if not, run a setup step before the real test.
   ART writes these as scripts that call `exit 0` / `exit 1` directly — which is fine when ART
   runs them in their own little sandboxed step, but would be dangerous to run inline in a
   Caldera ability, because `exit` would kill the *entire* ability's script the moment it runs,
   not just the check. To avoid that, the script wraps each prerequisite check in its own
   subprocess (a nested `powershell -EncodedCommand ...` or `cmd /c "..."` call) and only reads
   its exit code, so a `exit 1` inside the check can't take down the whole ability.

6. **Writes out one Caldera ability YAML file per test**, in the format Caldera expects (id, name,
   description, tactic, technique, and the platform-specific command/cleanup/payloads block).

Every generated ability also gets:
- A **new, unique ID** (not ART's original ID) so importing these doesn't collide with anything else.
- Its name prefixed with `Soumaia ART Tests - ` so it's easy to tell apart from other abilities
  in your Caldera library.

## Requirements

- Python 3.13 (or close to it)
- The `PyYAML` package (`pip install pyyaml`, if you don't already have it)
- The `atomic-red-team` submodule checked out — if `atomic-red-team/atomics/` looks empty, run:
  ```
  git submodule update --init --recursive
  ```

## How to run it

From the repo root:

```
python tools/generate_caldera_abilities.py
```

This processes **every** technique and writes the full set of abilities into `caldera-abilities/`
(deleting/overwriting anything already there from a previous run isn't automatic — delete the
folder yourself first if you want a totally clean run).

Useful flags:

| Flag | What it does |
|---|---|
| `--technique T1059.001` | Only process one technique folder — handy for testing a single case instead of waiting on the full ~1,800-test run. |
| `--dry-run` | Don't write any files — just print what *would* be generated, straight to your terminal. Good for sanity-checking before committing to a full run. |
| `--out-dir <path>` | Write output somewhere other than the default `caldera-abilities/` folder. |

Example — check one technique without writing anything:
```
python tools/generate_caldera_abilities.py --technique T1003.001 --dry-run
```

A full run over all techniques takes roughly a minute and prints a one-line summary when done,
e.g.:
```
Generated 1216 abilities into .../caldera-abilities
See .../caldera-abilities/generation-report.md for details
```

## What you get (output layout)

```
caldera-abilities/
  abilities/<tactic>/<ability-id>.yml         Ready-to-import abilities (the vast majority)
  _missing-payloads/<tactic>/<ability-id>.yml Abilities whose required helper file wasn't found
  _unmapped/<ability-id>.yml                  Abilities whose tactic couldn't be looked up
  payloads/<filename>                         Helper files (wordlists, scripts, tools) abilities depend on
  generation-report.md                        Summary + full list of anything that needed attention
```

- **`abilities/`** — the abilities you actually want. Organized into a subfolder per tactic
  (`persistence`, `stealth`, `credential-access`, etc.), matching how Caldera itself organizes
  its built-in ability library. These are safe to import as-is.
- **`_missing-payloads/`** — same format as `abilities/`, but each of these references a helper
  file that genuinely doesn't exist in the ART repo (a binary tool that isn't checked into git,
  for example). The ability YAML is still valid, it just won't have everything it needs to run
  until you track down and supply that file yourself.
- **`_unmapped/`** — a small number of tests ART's own index file doesn't list, so the script
  couldn't determine which tactic folder they belong under. They're still fully generated
  (command, payloads, everything) — just check the `technique:` field inside the file and file
  them under the right tactic by hand if you want to use them.
- **`payloads/`** — every helper file any ability needs, all in one flat folder (this mirrors how
  Caldera's own built-in ability pack, "Stockpile," organizes its payloads).
- **`generation-report.md`** — read this after every run. It tells you, in plain numbers, how many
  tests were skipped and why, which specific tests need a human to fill in a missing value, which
  ones have a missing payload, and any other warnings worth a second look.

## Importing into Caldera

Caldera only picks up ability/payload files it already knows to look in — usually a plugin's
`data/abilities/` and `data/payloads/` folders. The simplest path:

1. Copy the contents of `caldera-abilities/abilities/` into your Caldera install's
   `data/abilities/` folder (or package this whole `caldera-abilities/` folder as its own plugin —
   ask if you'd like a starter plugin scaffold for that).
2. Copy `caldera-abilities/payloads/` into `data/payloads/` the same way.
3. Restart Caldera (or reload plugins) so it re-scans and picks up the new files.
4. In the Caldera UI, abilities show up under **Abilities**, searchable by their
   `Soumaia ART Tests - ` name prefix.

Before trusting the whole batch, it's worth hand-importing 2-3 individual files first and
confirming Caldera loads them without errors — that's the one verification step that can't be
done from this environment.

## Re-running after ART updates

The script is safe to re-run any time (e.g. after pulling submodule updates). Ability IDs are
generated deterministically from the original ART test ID, so re-running produces the *same* IDs
each time — re-importing into Caldera updates the existing abilities rather than duplicating them.
