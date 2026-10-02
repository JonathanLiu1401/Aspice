# Headless Cadence: how `lib/cadence.py` works

Everything below was found by running the tools, on the UW ECE lab install
(Virtuoso IC23.1, Spectre 23.1.0.403, Calibre v2021.1_33.19, gpdk045 v6.0,
FreePDK45). Each item is a behaviour the backend depends on or works around.

## Discovery

| Thing | Order |
|---|---|
| A tool | `ASPICE_<TOOL>` env var -> `PATH` -> `ASPICE_EDA_ROOTS` (colon list) -> built-in roots (`/home/lab.apps/vlsiapps_new`, `/home/lab.apps/vlsiapps`, `/opt/cadence`, ...) |
| Licenses | environment, else `setenv` lines in `<root>/cshrc/licenses.cshrc` |
| Project | `$ASPICE_PROJECT` -> the registered project whose directory contains the cwd -> the active project (`use_project`) |
| cds.lib | `ASPICE_CDS_LIB` -> the project's cds.lib -> `cds_lib` from `configure()` -> `./cds.lib` -> `~/cds.lib` -> newest `~/*/cds.lib` |
| Extra env | the project's `env` (e.g. `PDK_DIR`) is applied to every tool run and to `$VAR` expansion in cds.lib |

Projects live in `~/.config/aspice/cadence.json`:
`CD.add_project('EE476', '~/EE476/cadence/cds.lib', env={'PDK_DIR': ...})`.
A csh `setenv` in `~/.cshrc` does not reach aspice (its tools run from Python,
not a login csh), which is why a project carries its own env.

Pin the cds.lib. Auto-picking the newest one silently switched projects
mid-session when a second course directory appeared; `doctor()` now lists
every candidate when none is pinned, and every library whose path does not
exist (including paths left with an unset `$PDK_DIR`).

Environment set for every tool run: `CDS`/`CDSHOME`, `CDS_LOAD_ENV=CWDElseHome`,
`CDS_Netlisting_Mode=Analog`, `SKIP_OS_CHECKS=1`, `MGC_HOME`/`CALIBRE_HOME`,
`USE_CALIBRE_VCO=aoj`, PATH with the Virtuoso, Spectre and Calibre bins, and
`CDS_LOG_PATH=<workdir>` so `CDS.log` never lands in the user's directory.

## Non-destructive by construction

- Each call gets its own workdir with a `cds.lib` that `INCLUDE`s the user's.
  Relative `DEFINE`s in the included file resolve against that file, so the
  user's libraries appear unchanged.
- Cellviews are opened `"r"`. schCheck, edits and builds act on a copy in
  `aspice_scratch`, defined inside the workdir.
- `edit_instance_params` raises `PermissionError` on any library except the
  scratch one unless `allow_write=True`.
- Spectre decks are copied (with sibling files such as `ade_e.scs`) into
  `<workdir>/netlist/input.scs`, results to `<workdir>/psf`: the ADE layout, so
  relative paths in ADE netlists (`sensfile="../psf/..."`) still resolve.
- Calibre gets copies of the GDS and source netlist.

## Tool behaviours

**OCEAN `createNetlist` ignores `resultsDir` for the netlist.** It writes to
`<projectDir>/<cell>/spectre/schematic/netlist`, and `projectDir` defaults to
`~/simulation`. Set it first:
`envSetVal("asimenv.startup" "projectDir" 'string "<workdir>/proj")`.

**OCEAN needs Spectre on PATH** even just to netlist (`spectre_encrypt`), and
hangs without a closed stdin. Both are handled by `_run`.

**ADE netlists default gpdk045 to `section=mc`.** `netlist_cell(section='tt')`
rewrites the include.

**Binary PSF -> ASCII:** `psf -f %.15e -i <file>` (default precision is 6
digits). ADE's transients are **PSF XL** (`tran.tran.tran.psfxl`): neither
`psf` nor `psfxl` converts them (the plain `tran.tran.tran` is a header stub
with zero points). OCEAN can: `openResults(dir) selectResult('tran)
ocnPrint(v("VOUT") ?output f)` - `read_ade_waveform` does this (7 significant
figures). `read_psf_dir` marks such stubs with a `note` pointing there.

**PSF-ASCII structure:** `HEADER`, `TYPE`, optional `SWEEP` + `TRACE`, `VALUE`,
`END`. Swept values come as `"sweepvar" x` then `"trace" v` per point; AC values
are `(re im)`. `info what=oppoint` records are `"inst" "bsim4" ( v1 v2 ... )`
whose field names are the `STRUCT(...)` declaration in `TYPE`. An inline subckt
(gpdk045's `g45n1svt`) reports as the instance itself (`NM0`), not `NM0.x`.
MOS `region`: 0 off, 1 triode, 2 saturation, 3 subthreshold, 4 breakdown.

**Spectre oppoint signs:** `cgs`/`cgd` are reported as charge derivatives and
come out negative; `ids` is negative for PMOS.

**SKILL pitfalls hit while writing this:**
- `t` is a protected symbol: `foreach(t ...)` fails, but only when the list is
  non-empty, so it passed on a pinless testbench and failed on a real cell.
- A freshly created instance has no `instTerms` until something connects to
  it; find terminals through `inst~>master~>terminals`.
- `virtuoso -nograph -replay` writes results to the log as `\o ...` lines; the
  backend prints `ASPICE|kind|field|...` records and parses those. Code runs
  inside `errset` so an error is reported instead of silently continuing.

**strmin needs a layer map.** gpdk045 ships `gpdk045.layermap`; FreePDK45 ships
none, but its Calibre `layer.inc` (`layer metal1 11`) carries the numbers, so
`find_layer_map` generates one.

**Kit Calibre decks reference `$PDK_DIR`.** FreePDK45's LVS deck does
`include $PDK_DIR/ncsu_basekit/techfile/calibre/layer.inc`; the lab's
`freepdk45.cshrc` points `PDK_DIR` at a path that no longer exists.
`rule_env` sets any such variable to the deck ancestor that actually holds the
referenced file.

**Kit decks may already set report statements.** FreePDK45's LVS deck has its
own `LVS REPORT mask.lvs.rep`; a runset that repeats it fails with "superfluous
specification statement". The runset generator drops statements the deck
already sets and reads the report name from the deck.

**LVS report:** the overall verdict is ASCII art; the `CELL SUMMARY` table has
it in words, and `INITIAL NUMBERS OF OBJECTS` has port/net/instance counts.

## Accuracy, measured

| Comparison | Result |
|---|---|
| Re-running stored ADE netlists in Spectre vs the stored PSF | identical to 3e-16 V (6 points), 1e-7 V via 6-digit PSF conversion before `-f %.15e` |
| Same PTM BSIM4 card, Spectre vs ngspice (biased CS stage) | node voltages within 77 uV (0.05%) |
| gpdk045 `g45n1svt` vs PTM 45nm HP, W=1u, Vds=0.6 | PTM current 3x (Vgs=1.0) to 60x (Vgs=0.3) higher at L=45n; 1.5x at L=180n; 0.7x at L=1u |
| PTM 45nm LP vs gpdk045 | matches at L=45n strong inversion, 10-20x low at L>=180n |
| Hand analysis from Spectre's gm/gds (CS stage, Av = gm(R_D \|\| r_o)) | 2.510 vs 2.5098 simulated |

Conclusion: the simulators agree; the models do not. Use the real PDK through
Spectre for anything that predicts a real design.
