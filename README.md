# Atomic Red Team → Caldera Abilities

This repo turns [Atomic Red Team](https://github.com/redcanaryco/atomic-red-team) (ART) tests into
[Caldera](https://github.com/mitre/caldera) **abilities** and **adversary profiles**, so you can run
ART's library of attack techniques as Caldera operations instead of copy-pasting commands by hand.

## What's an "Atomic Red Team test", a "Caldera ability", and a "Caldera adversary"?

- **Atomic Red Team** is a library of small, self-contained scripts that each simulate one
  technique attackers use (e.g. "dump credentials from memory", "add a registry run key for
  persistence"). Each technique is identified by a [MITRE ATT&CK](https://attack.mitre.org/) ID
  like `T1059.001`. ART stores these as YAML files with a command to run, an optional cleanup
  command, and sometimes a list of arguments you can customize.
- **Caldera** is an attack-emulation platform. You point it at a machine (running a Caldera
  "agent"), and it executes a sequence of **abilities** — also YAML files, but in Caldera's own
  format — against that machine, tracking what ran and what happened.
- A Caldera **adversary** is just a named, ordered list of abilities — think of it as a
  ready-to-run "playbook" bundling many individual abilities into one thing you can launch as a
  single operation, instead of picking abilities one at a time.
- The ART and Caldera formats are different enough that you can't just hand Caldera an ART file.
  This repo's job is to translate ~1,800 ART test definitions into ~1,200 ready-to-use Caldera
  ability files, plus one adversary profile per ATT&CK tactic bundling all the abilities for that
  tactic together (some tests don't apply to Windows, or can't be run automatically — more on that
  below).

## Repo layout

```
atomic-red-team/        Git submodule — the original ART test library (source data, untouched)
tools/
  generate_caldera_abilities.py   The conversion script (see below)
  validate_ps_syntax.py            Checks every generated PowerShell command actually parses
  validate_cmd_syntax.py           Best-effort structural check for generated cmd.exe commands
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
   as-is, no manual editing needed. (A small number of placeholders have no sensible default at
   all — ART itself just says "fill this in" — those are left as-is and flagged in the report;
   see "manual input" below.)

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

6. **Flattens everything to one line.** Caldera expects each ability's `command` (and each
   `cleanup` entry) to be a single, directly-executable line — not a multi-line script block. ART
   itself writes tests as ordinary multi-line scripts, so the generator collapses each one down:
   comment-only lines are dropped, and remaining lines get joined with `;` (PowerShell) or `&`
   (cmd) — except where that would actually break the script (see "Why flattening is the trickiest
   part" below).

7. **Writes out one ability YAML file per test**, in the flat schema Caldera expects: `name`,
   `description`, `tactic`, `technique_id`, `technique_name`, `id`, and an `executors` list (one
   entry, containing `platform`, `name` (`psh` or `cmd`), `command`, `cleanup`, `payloads`,
   `parsers`, and a `timeout`).

8. **Bundles every clean ability into an adversary profile, one per tactic.** After all abilities
   are generated, everything that landed in `abilities/` (i.e. nothing missing a payload or an
   unresolved tactic) gets grouped by tactic into `adversaries/<tactic>.yml` — so you can run "every
   generated persistence technique" as one operation instead of hand-picking abilities.

Every generated ability also gets:
- A **new, unique ID** (not ART's original ID, and not just a copy of it either — it's derived
  from both the ability's name *and* ART's original ID) so importing these doesn't collide with
  anything else already in your Caldera instance, including other tools that also derive IDs from
  ART's own GUIDs.
- Its name prefixed with `Soumaia ART Tests - ` so it's easy to tell apart from other abilities
  in your Caldera library.

### Why flattening is the trickiest part

Turning an arbitrary multi-line PowerShell or batch script into one valid line sounds simple but
has real sharp edges, and most of the bugs found and fixed in this generator lived here:

- A comment line (`# ...`) can't just be dropped in place — if you naively join the next line onto
  it with `;`, the `#` swallows everything after it on that "line" (since `#` comments run to the
  end of the line, and after flattening there's no real end-of-line anymore) and the rest of the
  script silently vanishes.
- Some constructs *must* stay on separate lines from a syntax standpoint — a `try { }` block can't
  have a `;` inserted right before `catch`, an `if (...)` can't have a stray `;` inserted right
  before its own opening `{`, and a multi-line `param (...)` list can't have a `;` inserted between
  its arguments — all of these need a plain space instead, not a statement separator.
  PowerShell "here-strings" (`@"..."@`) are a more extreme case: their delimiters are physically
  tied to line boundaries, so they get converted into an ordinary quoted string with escaped
  newlines *before* flattening, rather than flattened directly.
- A trailing `|` (pipe) at the end of a line means "the pipeline continues on the next line" —
  inserting `;` right after it creates an empty, invalid pipe segment.

See `tools/validate_ps_syntax.py` below for how this is actually verified, rather than just hoped
for.

## Requirements

- Python 3.13 (or close to it)
- The `PyYAML` package (`pip install pyyaml`, if you don't already have it)
- The `atomic-red-team` submodule checked out — if `atomic-red-team/atomics/` looks empty, run:
  ```
  git submodule update --init --recursive
  ```
- Windows PowerShell (available by default on Windows), only needed to run
  `tools/validate_ps_syntax.py` — the generator itself doesn't need it.

## How to run it

From the repo root:

```
python tools/generate_caldera_abilities.py
```

This processes **every** technique and writes the full set of abilities and adversary profiles
into `caldera-abilities/` (deleting/overwriting anything already there from a previous run isn't
automatic — delete the folder yourself first if you want a totally clean run).

Useful flags:

| Flag | What it does |
|---|---|
| `--technique T1059.001` | Only process one technique folder — handy for testing a single case instead of waiting on the full ~1,800-test run. |
| `--dry-run` | Don't write any files — just print what *would* be generated, straight to your terminal. Good for sanity-checking before committing to a full run. |
| `--out-dir <path>` | Write output somewhere other than the default `caldera-abilities/` folder. |
| `--timeout <seconds>` | Timeout set on every generated ability's executor (default: 60). |

Example — check one technique without writing anything:
```
python tools/generate_caldera_abilities.py --technique T1003.001 --dry-run
```

A full run over all techniques takes roughly a minute and prints a summary when done, e.g.:
```
Generated 1216 abilities into .../caldera-abilities
Generated 13 adversary profiles into .../caldera-abilities/adversaries
See .../caldera-abilities/generation-report.md for details
```

**After every run**, it's worth running the two validators described below — they catch a real
class of bug (mostly in oddly-formatted or unusual ART source scripts) that the generator itself
can't always guarantee it handled correctly.

## What you get (output layout)

```
caldera-abilities/
  abilities/<tactic>/<ability-id>.yml         Ready-to-import abilities (the vast majority)
  adversaries/<tactic>.yml                    One adversary profile per tactic, bundling all its abilities
  _missing-payloads/<tactic>/<ability-id>.yml Abilities whose required helper file wasn't found
  _unmapped/<ability-id>.yml                  Abilities whose tactic couldn't be looked up
  payloads/<filename>                         Helper files (wordlists, scripts, tools) abilities depend on
  generation-report.md                        Summary + full list of anything that needed attention
```

- **`abilities/`** — the abilities you actually want. Organized into a subfolder per tactic
  (`persistence`, `stealth`, `credential-access`, etc.), matching how Caldera itself organizes
  its built-in ability library. These are safe to import as-is. Each file looks like:
  ```yaml
  - requirements: []
    name: Soumaia ART Tests - <original ART test name>
    description: <resolved description text>
    tactic: <tactic>
    technique_id: T1234.001
    technique_name: <ATT&CK technique name>
    executors:
    - cleanup: []                  # or a one-item list with a cleanup command
      timeout: 60
      platform: windows
      name: psh                    # or 'cmd'
      payloads: []                 # filenames staged from caldera-abilities/payloads/
      parsers: []
      command: <fully resolved, single-line command>
    id: <generated unique id>
  ```
- **`adversaries/`** — one file per tactic, each containing a single adversary profile (`name`,
  `description`, `atomic_ordering` — the list of ability IDs to run, in generation order —
  `adversary_id`). Only abilities that landed in `abilities/` (nothing missing a payload or an
  unresolved tactic) are included, so every adversary here is made entirely of ready-to-run
  abilities.
- **`_missing-payloads/`** — same format as `abilities/`, but each of these references a helper
  file that genuinely doesn't exist in the ART repo (a binary tool that isn't checked into git,
  for example). The ability YAML is still valid, it just won't have everything it needs to run
  until you track down and supply that file yourself.
- **`_unmapped/`** — a small number of tests ART's own index file doesn't list, so the script
  couldn't determine which tactic folder they belong under. They're still fully generated
  (command, payloads, everything) — just check the `technique_id` field inside the file and file
  them under the right tactic by hand if you want to use them.
- **`payloads/`** — every helper file any ability needs, all in one flat folder (this mirrors how
  Caldera's own built-in ability pack, "Stockpile," organizes its payloads).
- **`generation-report.md`** — read this after every run. It tells you, in plain numbers, how many
  tests were skipped and why, which specific tests need a human to fill in a missing value, which
  ones have a missing payload, and any other warnings worth a second look.

## Validating the generated commands

Flattening a script down to one line (see above) is the part most likely to introduce a subtle
bug — something that *looks* fine but won't actually run. Two tools check for that after the fact,
one for each shell:

### `tools/validate_ps_syntax.py` — real syntax checking for PowerShell (`psh`) abilities

```
python tools/validate_ps_syntax.py
```

PowerShell ships with its own parser as a public API
(`[System.Management.Automation.Language.Parser]::ParseInput`) that builds a full parse tree from
a script **without running it**. This tool pulls every `psh` executor's `command` and `cleanup`
text out of every generated YAML file, feeds each one through that real parser, and reports any
that fail to parse — with PowerShell's own error message, so you know exactly what's wrong and
where. Because it's backed by the actual PowerShell engine's own parser, a clean pass here is a
genuine guarantee the command is syntactically valid, not a guess.

(One implementation detail worth knowing: the snippets are piped to PowerShell over stdin rather
than written to a temp file first — a file containing a pile of concatenated ART one-liners, like
several different Mimikatz download stagers back to back, reliably gets flagged and blocked by
real-time antivirus the moment anything tries to read it. Piping avoids ever writing that file.)

A run currently reports one intentional "failure" — a test whose ART source has no real default
value for a required argument (`T1030`'s `source_file_path`, which ART itself sets to the literal
text `"[User specified]"` rather than a usable path). That's correct behavior, not a bug: the
placeholder is deliberately left unresolved so a human has to supply a real value, and it's also
listed in `generation-report.md` under "Tests needing manual input." Any *other* failure reported
here is a real bug worth investigating.

### `tools/validate_cmd_syntax.py` — best-effort checking for Command Prompt (`cmd`) abilities

```
python tools/validate_cmd_syntax.py
```

Unlike PowerShell, `cmd.exe` has no public parser API and no dry-run/syntax-check mode at all —
there's genuinely no way to ask "would this be valid" without actually running it. (There's an
undocumented internal debug flag, `fDumpParse`, that malware analysts use to expose cmd.exe's own
parse tree — but it only works while the command is actually executing under a specialized
debugger harness, which isn't something to run in bulk over commands that delete files, edit the
registry, or download tools.)

So this tool is intentionally weaker, and says so in its own docstring: it's a handful of plain
structural checks (balanced parentheses, balanced quotes, balanced `%variable%` references, no
dangling `&`/`|` at the start or end of a command, no empty `( )` blocks) that catch the same
*category* of bug the PowerShell checker catches for real, without the same guarantee. Passing
every check is evidence the command isn't obviously broken, not proof it's valid cmd.exe syntax.

Two cmd constructs genuinely cannot survive being flattened to one line at all — `goto`/`:labels`
(jump targets tied to a physical line position) and caret (`^`) line-continuation. Before writing
this tool, every Windows `cmd`-executor test in the ART corpus was checked for both, and neither
appears anywhere — so that specific risk is ruled out structurally, not just hoped away.

Expect a handful of flagged results that are **known false positives**, not real bugs:
- **"unbalanced %"** — batch `FOR /F %p in (...)` loop variables are single-percent by design
  (unlike `%VAR%` environment variables); using one 3 times naturally produces an odd `%` count
  with completely valid syntax.
- **"empty ( )"** — some ART tests embed a small JScript/VBScript payload via `echo ... > file.js`;
  no-argument method calls like `.Close()` or `.Exec()` inside that embedded script look identical
  to an empty `IF ( )` block to a heuristic that isn't a real parser.

## Importing into Caldera

Caldera only picks up ability/payload/adversary files it already knows to look in — usually a
plugin's `data/abilities/`, `data/payloads/`, and `data/adversaries/` folders. The simplest path:

1. Copy the contents of `caldera-abilities/abilities/` into your Caldera install's
   `data/abilities/` folder (or package this whole `caldera-abilities/` folder as its own plugin —
   ask if you'd like a starter plugin scaffold for that).
2. Copy `caldera-abilities/payloads/` into `data/payloads/` the same way.
3. Copy `caldera-abilities/adversaries/` into `data/adversaries/` the same way, if you want the
   bundled per-tactic profiles too (optional — importing just `abilities/` is enough if you'd
   rather build your own operations ability-by-ability).
4. Restart Caldera (or reload plugins) so it re-scans and picks up the new files.
5. In the Caldera UI, abilities and adversaries show up searchable by their
   `Soumaia ART Tests - ` name prefix.

Before trusting the whole batch, it's worth hand-importing 2-3 individual files first and
confirming Caldera loads them without errors — that's the one verification step that can't be
done from this environment.

## Extracting test-run results (`Data/`)

Once you've run the generated abilities against a target, Caldera gives you a report for that
operation. The scripts in `Data/` turn that report into an Excel workbook — one row per ability,
with its status, command, stdout, and stderr — instead of you reading it out of Caldera's UI or
raw JSON by hand.

Caldera can give you two different artifacts per operation, and they're not equally complete:

- **`<name>_report.json`** — always available. A structured report of every ability that ran, but
  in practice its `output.stdout`/`output.stderr` fields are frequently empty even when the
  ability clearly produced output.
- **`<name>.html`** — only available if you exported it from Caldera's UI. It renders the same
  abilities but with the *actual* Standard Output/Standard Error text Caldera captured, which is
  often present here even when it's missing from the JSON.

Because coverage differs per folder — some technique folders only have the JSON, others have
both — there are three scripts, but you'll normally only run one:

### `Data/collect_reports.py` — the one to run

Walks a folder tree, finds every `*_report.json`, groups them by tactic folder (any top-level
`<tactic>-done` folder under `Data/` — `credential-access-done`'s `run1`-`run4`, `stealth-done`'s
`run1` and `run2/p1-p3`, etc. all roll up into their shared `<tactic>-done` ancestor), and writes
**exactly one workbook per tactic folder**, always named `<tactic>-done.xlsx` and written at that
tactic folder's root — regardless of whether it had one report or several. There's no per-run or
per-report output file; everything for a tactic lands in that one sheet.

For each report that feeds into a tactic's workbook:
- If there's no matching HTML in the same folder, its JSON steps are used directly.
- If there's a matching HTML report too, both are extracted and **merged**: `Ability Name`/`TTP`/
  `Status`/`Command` come from the JSON (it's the structured source of truth), while `Output`/
  `Error` prefer the HTML's text and only fall back to the JSON's when the HTML has nothing
  (Caldera renders `"Nothing to show"` for an empty stream).

Every tactic's workbook is a flat stack of that tactic's rows across all its runs, six columns:
`Ability Name`, `TTP`, `Status`, `Command`, `Output`, `Error`.

```
python Data/collect_reports.py [path] [--output-dir DIR] [--dry-run]
```

| Flag | What it does |
|---|---|
| `path` | Folder to scan, recursively (default: `Data`). Point it at the whole tree or a single tactic/run folder — tactic grouping is based on the `-done` folder-naming convention itself, so it works the same way either way. |
| `--output-dir <path>` | Write outputs under this folder instead of at each tactic folder's root. |
| `--dry-run` | Print what would be read and which tactic workbook it would feed into, without writing anything. Worth running once before a full pass. |

Example — check what a full run would do first, then do it:
```
python Data/collect_reports.py Data --dry-run
python Data/collect_reports.py Data
```

**`Data/analysis-summary.md`.** After writing every tactic workbook, `collect_reports.py` also
writes a birds-eye markdown summary across all of them (skipped on `--dry-run`) — an overview
(total tactics/reports/steps/TTPs/abilities, total OpenSearch log entries, overall success rate),
a per-tactic breakdown table (ability-execution steps, OpenSearch log entries, unique TTPs, unique
abilities, success/failure/timeout counts), a data-completeness check (how often a real Standard
Output/Error was actually captured vs. left as a placeholder — the same concern the row-leak/Facts
bugs above were about), a "most-tested TTPs" highlight list, and a full per-TTP table of
**procedures** — the number of *distinct commands* seen for each technique ID, i.e. how many
different ways this repo exercises that technique, sorted most-tested first.

The "OpenSearch Logs" counts come from each tactic folder's raw OpenSearch export(s) — files named
like `*os-logs*.json`, or (`stealth-done`'s paginated exports) `page*.json` — summed across every
matching file found anywhere under that tactic folder. These are a separate, much larger data
source than the ability-execution steps: raw log records collected from the target during the
operation, not one row per ability run.

### `Data/parse_collection_steps.py` / `Data/extract_html.py` — the individual extractors

`collect_reports.py` calls these two internally, but each is also a standalone CLI if you just
want one file converted without any merge logic:

```
python Data/parse_collection_steps.py <report.json> [-o output.xlsx]
python Data/extract_html.py <report.html> [-o output.xlsx]
```

Both default to writing the `.xlsx` alongside the input file if `-o`/`--output` isn't given.

### Requirements for these three scripts

```
pip install beautifulsoup4 openpyxl xlsxwriter
```

(`.xlsx` files are written with `xlsxwriter` in its `in_memory` mode rather than `openpyxl`'s
default save path, because openpyxl stages each worksheet as its own file in `%TEMP%` before
zipping it up — and real-time antivirus has been observed quarantining that staging file outright
as a false positive, since its content is literally raw attack-technique commands and output.
`in_memory` mode builds the workbook in RAM and writes the finished `.xlsx` in one shot, which
avoids creating that intermediate file at all.)

## Re-running after ART updates

The script is safe to re-run any time (e.g. after pulling submodule updates). Ability and
adversary IDs are generated deterministically (from each ability's name plus ART's original test
ID), so re-running produces the *same* IDs each time — re-importing into Caldera updates the
existing abilities/adversaries rather than duplicating them. Re-run both validators after
regenerating, too — an ART update could introduce a script shape the flattening logic hasn't seen
before.
