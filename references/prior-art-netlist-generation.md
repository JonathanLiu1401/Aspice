# Prior art: Python-to-netlist generation for analog IC / Cadence

Research notes for the generate-here (Windows, no Cadence) / export-to-Linux-Virtuoso workflow.

**Written:** 2026-09-01. **Method:** live GitHub API + raw README / source fetches. Star counts, licenses, and `pushed_at` dates below are from `api.github.com/repos/...` on that day. No Cadence, no `spiceIn`, and no Spectre binary were available on this machine, so Cadence-side behavior is from project source and docs, not a live Virtuoso run.

**Do not treat this as a license opinion.** SPDX values are GitHub's `license.spdx_id` field unless a LICENSE file was read.

---

## Ranked shortlist: if you want X, use Y

1. **If you want a Virtuoso schematic from Python (the user's stated example):** use [KwantaeKim/cdl_gen](https://github.com/KwantaeKim/cdl_gen). It writes CDL, maps generic `l` / `w_f` / `n_f` / `w_tot` through `techmap.json` to PDK CDF names, then calls Cadence `spiceIn` and rebuilds placement from JSON. Closest existing match. It cannot run on this Windows box as shipped: `__init__.py` exits if `spiceIn` is missing.

2. **If you want a Cadence-free analog HDL that emits Spectre or SPICE for simulation:** use [Hdl21](https://github.com/dan-fritchman/Hdl21) + [VLSIR](https://github.com/Vlsir/Vlsir). Python `Module` / generators, PDK plugins (`sky130-hdl21`, `gf180-hdl21`, `ihp-hdl21`), `h.netlist(..., fmt="spice"|"spectre"|"verilog"|"xyce")`. No OA, no Virtuoso schematic, no advertised CDL writer.

3. **If you want an agent on Windows to drive a live Virtuoso / Spectre session on Linux:** use [Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) (692 stars, remote SSH). [unihd-cag/skillbridge](https://github.com/unihd-cag/skillbridge) is the thinner local-only SKILL RPC. Neither generates a netlist by itself.

4. **If you want a full generator that already lives inside Cadence (schematic + layout + CDL/Spectre):** use BAG3++ ([ucb-art/bag](https://github.com/ucb-art/bag), workspaces under `ucb-art/bag3_*`). Process-agnostic generators plus `BAG_prim` YAML. Opposite of generate-here: it assumes Virtuoso on the design server.

5. **If you want open-source schematic capture that already emits SPICE and Spectre:** use [Xschem](https://github.com/StefanSchippers/xschem). Batch netlist is real. The netlist is only Cadence-importable if device names and CDF params match the Linux PDK. Sky130 / IHP names will not spiceIn into a commercial TSMC-style library.

6. **If you want to keep simulating here with the existing skill:** stay on PySpice / `analog_spice` (already patched in this repo). Do not adopt SKiDL as the IC path: it is a KiCad / PCB netlist tool. The reverse import (ADE `.scs` -> digest / ngspice) is already this skill's job (`spectre_netlist`, `spectre_to_ngspice`).

---

## Approaches considered (before ranking)

Do not tunnel on "just emit Spectre." These are different products:

| Approach | What it optimizes | Fatal edge if you pick it alone |
|---|---|---|
| A. Emit Spectre from Hdl21 / PySpice | Local ngspice / remote ADE sim | `spiceIn` wants CDL/SPICE with PDK *cell* names and CDF params, not Spectre-native `M0 (d g s b) nch w=...`. A Spectre deck will simulate; it will not become a clean schematic. |
| B. Emit CDL + techmap + placement JSON (cdl_gen style) | Virtuoso schematic on Linux | `spiceIn` placement is tangled without JSON. Import-time `spiceIn` requirement blocks Windows generation unless the writer is split off. |
| C. Remote SKILL (skillbridge / virtuoso-bridge-lite) | Live OA edits, Maestro, Spectre PSF | Needs a running Virtuoso. Not a standalone generator. OA-from-Python without SKILL is the path Cadence staff told people not to take. |
| D. Xschem as the schematic home | Open PDK flow, SPICE+Spectre netlist | Device names are open-PDK (`sky130_fd_pr__nfet_01v8`), not commercial CDF cells. |
| E. BAG3++ on the Linux box | Tapeout-grade generators | Heavy C++/OA, Virtuoso-bound, last framework push late 2024. Not a Windows IR. |
| F. Extend this skill's `Circuit` / Spectre IR to also write CDL | Reuse existing parser | The IR already understands ADE dumps. It does not know PDK lib/cell/CDF names. That map is the missing piece, not another parser. |

Recommended composition for this user: **F or a thin Hdl21-like DSL for topology + B for export + C only when you must poke a live session.** Keep A for local sim. Do not replace the existing Spectre parser.

Edges any exporter must survive (see also `ade-netlist-errors.md` and `spectre-syntax.md`):

- CDF `w` is finger width in one PDK and total width in another (`cdl_gen` README states this explicitly).
- MOSFET terminal order is not universal (cdl_gen uses `[D, G, S, B]`).
- Subckt pin order is positional in CDL/SPICE; a swapped list silently miswires.
- Spectre `M` is mega; SPICE `M` is milli; ngspice trailing `A` is atto.
- `simulator lang=spectre` / `lang=spice` mid-file.
- `include ... section=tt` missing or misspelled.
- `spiceIn` needs `-reflibList` pointing at the PDK library or instances become unbound.
- After `spiceIn`, PDK CDF callbacks may need a finger toggle so `w_tot` recomputes (`cdlgenSetFingers`).
- `spiceIn` does not place; schematics come out tangled.

---

## 1. Python-to-netlist / circuit-as-code

### 1.1 cdl_gen (KwantaeKim)

- **Repo:** https://github.com/KwantaeKim/cdl_gen
- **License:** MIT (GitHub SPDX).
- **Language:** Python + SKILL (`cdl_gen.il`).
- **Last activity:** `pushed_at` 2026-08-25. 31 stars, 8 forks, not archived. Small (repo size 123). Alive, early, author-driven.
- **What it actually does:** `device` + `subckt` objects serialize to CDL (`.SUBCKT` / instance line / `.ENDS`). `techmap.json` maps generic sizing to PDK CDF names. `spicein()` shells Cadence `spiceIn`. `placesch()` rebuilds a schematic from JSON via `virtuoso -nograph` + SKILL. `extractsch()` snapshots a hand-edited schematic back into `wire_ext`. `createlib()` registers a library in `cds.lib`. Templates: capacitor, cap DAC, 5T OTA.
- **Formats:** Writes CDL (SPICE-like). Reads/writes placement JSON. Does not write Spectre, OpenAccess, or GDS. CDF is written as `name=value` on the CDL instance line, then again as SKILL `cdfGetInstCDF` sets during placement.
- **Cadence required:** Yes, as shipped. `__init__.py` does `sys.exit(1)` if `shutil.which("spiceIn")` is None. `write_cdl()` itself is pure Python, but you cannot import the package without Cadence on PATH.
- **PDK abstraction:** One file, `techmap.json`. Observed default (gpdk045):

```json
"lib": "gpdk045",
"cells": {"nmos": "nmos1v", "pmos": "pmos1v"},
"params": {"l": "l", "w_f": "fw", "n_f": "fingers", "w_tot": "w"}
```

  Generic names: `l`, `w_f`, `n_f`, optional `w_tot = w_f * n_f`. Helper `cdlgenDumpCDF` prints CDF slots from the CIW so you match by prompt, not by guessed name.

- **Fit for generate-here / export:** This is the pattern to copy. Split the repo into (1) a Cadence-free CDL + techmap writer that runs on Windows, and (2) a Linux `spiceIn` + `placesch` runner. Do not vendor the import-time `spiceIn` check.
- **Weaknesses:** Tiny API (no hierarchy helpers, no simulation, no unit tests visible in the file tree). Placement JSON is a second source of truth and easy to desync. `ota_5t_beta.py` (wrapper cells) is marked unverified by the author. No Spectre emission. No model cards. Import-time Cadence dependency.

CDL instance form from `device.py` (verified):

```
{name} {terminals...} {model} {key}={val} ...
```

Subckt form from `subckt.py` (verified):

```
.SUBCKT {name} {pins} {key}={val} ...
...
.ENDS
```

---

### 1.2 Hdl21 + VLSIR (dan-fritchman)

- **Repos:** https://github.com/dan-fritchman/Hdl21 (96 stars, BSD-3-Clause, Python, `pushed_at` 2026-02-17); https://github.com/Vlsir/Vlsir (40 stars, BSD-3-Clause, `pushed_at` 2026-02-15). PyPI: `hdl21` 7.0.0, `vlsir` / `vlsirtools` 7.0.0 (vlsirtools sdist uploaded 2024-12-12). Not abandoned; not a large community either. 48 open Hdl21 issues.
- **What it actually does:** Python analog HDL. `Module`, `Signal`/`Port`, generators, `Prefixed` numbers (avoids float rounding into netlist strings). Compiles to VLSIR protobuf. `hdl21.netlist(mod, dest, fmt=...)` uses VlsirTools writers. `hdl21.sim` drives Spectre / Xyce / ngspice. PDK packages in-tree: ASAP7, Sky130, GF180, IHP.
- **Formats:** Advertised writers: SPICE, Spectre, Verilog, Xyce. Intermediate: VLSIR protobuf. Related: [hdl21schematics](https://github.com/Vlsir/hdl21schematics) (17 stars, last push 2024-01-24, stale) and [Layout21](https://github.com/dan-fritchman/Layout21) (62 stars, Rust, last push 2025-02-25). **CDL writer: not advertised. Treat as absent / unverified.** No OpenAccess, no GDS from Hdl21 itself.
- **Cadence required:** No. Spectre is optional for the sim driver.
- **PDK abstraction:** Generic `hdl21.primitives.Mos` (type / family / vth) compiled by `sky130_hdl21.compile()` etc. into `ExternalModule`s whose names match the open PDK (`NMOS_1p8V_STD` -> `sky130_fd_pr__...`). Model files are *not* shipped; you point `PdkInstallation` at a local `sky130.lib.spice`. There is no TSMC / Samsung / gpdk plugin in the public tree.
- **Fit:** Best standalone IR for "describe a circuit here, emit a deck." For Virtuoso schematic import you still need a CDL + CDF techmap backend that does not exist in VLSIR. Emitting `fmt="spectre"` is useful for ADE *simulation* of a text deck, not for `spiceIn`.
- **Weaknesses:** Python pin `<3.13` on the 7.0.0 wheels. PDK story is open-source nodes only. Schematics project is stale. VlsirTools README still says `FIXME!` on netlisting details. Spectre driver exists; this machine cannot verify it. No `spiceIn` path.

---

### 1.3 SKiDL (devbisme)

- **Repo:** https://github.com/devbisme/skidl
- **License:** MIT. Python. 1640 stars, `pushed_at` 2026-08-20. Mature, alive, PCB-oriented.
- **What it actually does:** Python description of *board* circuits. ERC. Emits KiCad netlists, XML BOMs, SVG/DOT, KiCad 6-10 schematics. Optional SPICE via InSpice (SKiDL 2.3 notes InSpice replaced PySpice). `netlist_to_skidl` for KiCad -> Python.
- **Formats:** KiCad netlist / `.kicad_sch`, XML, SVG, DOT. SPICE only as a simulation side door. Not CDL, not Spectre, not OA, not GDS.
- **Cadence required:** No.
- **PDK abstraction:** KiCad symbol libraries and footprints. No IC PDK / CDF map.
- **Fit:** Wrong problem. Useful only as a warning: "circuit-as-code" popularity is PCB, not analog IC. Do not build the IC IR to look like SKiDL parts/pins.
- **Weaknesses:** For this workflow, all of it.

---

### 1.4 PySpice and InSpice

- **PySpice:** https://github.com/PySpice-org/PySpice (same as FabriceSalvaire/PySpice). GPL-3.0. 863 stars, `pushed_at` 2026-09-01. Alive. This skill already vendors a patched import (`analog_spice`) against PySpice 1.4.3 + ngspice 44.2.
- **InSpice:** https://github.com/Innovoltive/InSpice (also `insim-ai/InSpice`). GitHub license field **AGPL-3.0**. 14 stars, `pushed_at` 2026-08-14. Fork of PySpice for newer ngspice. SKiDL switched to it.
- **What they do:** Object API (`Circuit`, `Mosfet`, sources) that *is* a SPICE netlist. `cir2py` parses a deck back to Python. Shared-library ngspice / Xyce. Partial SPICE parser. KiCad-schema experiment in-tree.
- **Formats:** ngspice / Xyce SPICE text. Not CDL, not Spectre-native, not OA.
- **Cadence required:** No.
- **PDK abstraction:** You `.include` a model file and name the model (`nch`, `sky130_fd_pr__nfet_01v8`). No CDF rename layer.
- **Fit:** Correct local simulator binding. Wrong Cadence schematic export. A `Circuit.str()` dump is a starting point for a CDL writer only after a techmap pass.
- **Weaknesses:** Already documented in `pyspice-cookbook.md` (stderr-as-fatal, version table). InSpice AGPL is a license step-up from PySpice GPL if you vendor it. Neither knows Virtuoso cells.

---

### 1.5 Lcapy (mph-)

- **Repo:** https://github.com/mph-/lcapy
- **License:** LGPL-2.1. Python. 300 stars, `pushed_at` 2026-08-16. Alive, academic / teaching.
- **What it does:** Symbolic linear circuit analysis (SymPy). SPICE-*like* netlist *input*. `netlist()` dumps Lcapy's own drawing netlist (includes `W` wires and schematic hints). Circuitikz / SVG schematics. Numerical time-stepping for linear RLC only; docs send nonlinear work to PySpice.
- **Formats:** Reads SPICE-like text. Writes Lcapy netlist + LaTeX/SVG. Not CDL, not Spectre, not MOSFET PDK decks.
- **Cadence required:** No.
- **PDK abstraction:** None.
- **Fit:** Useful as an LLM reasoning aid for *linear* transfer functions, not as a Cadence exporter.
- **Weaknesses:** No BSIM devices. "Netlist" does not mean foundry CDL.

---

### 1.6 schemdraw (cdelker)

- **Repo:** https://github.com/cdelker/schemdraw
- **License:** MIT. 264 stars, `pushed_at` 2026-07-18.
- **What it does:** Textbook-style schematic drawings (Python -> SVG/matplotlib). Optional DSP / analog element sets.
- **Formats:** Pictures. Not a netlist.
- **Cadence required:** No.
- **Fit:** Documentation figures only. Do not confuse with schematic *capture*.

---

### 1.7 nimphel, CircuitBrew, circuitsilk, "PyCircuit", CircuitPython

Searched. Results:

- **[servinagrero/nimphel](https://github.com/servinagrero/nimphel)** ("Netlist Invention and Manipulation with Python Embedded Language"). MIT. 4 stars, last push 2024-04-02. Academic paper (Electronics 2023). Parametric SPICE generation for variability campaigns. Thin, quiet. Prior art, not a base.
- **[virantha/circuitbrew](https://github.com/virantha/circuitbrew).** Apache-2.0. 0 stars, last push 2023-05-08. Transistor-level Python + HSPICE + Verilog-A test vectors. Dead for this purpose.
- **circuitsilk** exists on PyPI (Sky130, ngspice, design-space sweep). GitHub path `Levant-Labs/circuitsilk` 404'd on 2026-09-01. **Source repo unverified.** Do not depend on it.
- **PyCircuit:** No living analog-IC repo of that name was found that emits Cadence/SPICE/CDL. Do not invent one.
- **CircuitPython:** Microcontroller firmware runtime (Adafruit). Not a circuit DSL. Ignore the name collision.

---

## 2. Cadence-adjacent automation

### 2.1 skillbridge (unihd-cag)

- **Repo:** https://github.com/unihd-cag/skillbridge
- **License:** LGPL-3.0. Python. 340 stars, `pushed_at` 2026-03-23. Mature RPC, still maintained.
- **What it does:** `ipcBeginProcess` + `evalstring` SKILL server inside Virtuoso. Python `Workspace` maps `ws.db.open_cell_view_by_type(...)`. Tab completion / stubs. Requires IC 6.1.7+ and a *running* Virtuoso with `pyStartServer`.
- **Formats:** None of its own. It speaks SKILL, which then speaks OA.
- **Cadence required:** Yes, local session. README does not advertise SSH.
- **PDK abstraction:** Whatever the open cellview's CDF already is.
- **Fit:** Fine if the agent sits on the Linux box next to Virtuoso. Not a generate-here exporter. Cadence community guidance (forum thread "Edit schematic with python") is: do not poke `sch.oa` with unsupported OA Python bindings; use SKILL. skillbridge is that path.
- **Weaknesses:** Local only. Raw SKILL for schematic create. No netlist writer. No Spectre runner.

---

### 2.2 virtuoso-bridge-lite (Arcadia-1)

- **Repo:** https://github.com/Arcadia-1/virtuoso-bridge-lite (this is the live project: 692 stars, MIT, `pushed_at` 2026-08-30). [DavidQyz/virtuoso-bridge-lite](https://github.com/DavidQyz/virtuoso-bridge-lite) and [sea-monsters/virtuoso-bridge-lite](https://github.com/sea-monsters/virtuoso-bridge-lite) are 0-star copies; ignore them.
- **What it does:** Same SKILL IPC mechanism as skillbridge, plus SSH tunnel / jump host, CLI (`virtuoso-bridge eval/load/status`), schematic/layout helpers, Maestro snapshot (pulls `netlist/input.scs`), standalone Spectre + PSF parse, agent skill files. README claims Windows / macOS / Linux clients. Authors: Tsinghua (Zhang / Li / Sun / Jie), 2025 paper draft.
- **Formats:** SKILL in, OA in Virtuoso. Can import CDL via spiceIn *if you send that SKILL* (README comparison table lists "import CDL via spiceIn" as a schematic feature; I did not execute it). Exports Maestro Spectre decks and Visio. Not a Python circuit DSL.
- **Cadence required:** Yes, on the remote host. Client is standalone.
- **PDK abstraction:** None in the bridge. You still need a techmap if you generate instances.
- **Fit:** Best existing "Windows agent, Linux Virtuoso" *transport*. Pair with a CDL writer; do not use the bridge as the circuit IR.
- **Weaknesses:** Needs a live Virtuoso + daemon load in CIW. Stringly-typed SKILL (no skillbridge-style mapping). Agent-oriented surface area is large; treat unvetted SKILL helpers as unsafe on a tapeout library. 692 stars is marketing-heavy; I did not run the tunnel.

---

### 2.3 BAG / BAG3++

- **BAG2-era framework:** https://github.com/ucb-art/BAG_framework (172 stars, BSD-3-Clause, last push **2022-12-04**). Stale.
- **BAG3++ tree:** https://github.com/ucb-art/bag (39 stars, mixed Apache-2.0 / BSD-3-Clause per README, last push **2024-12-27**). Docs: https://github.com/ucb-art/bag3_readthedocs (last push 2023-10). Workspaces: `ucb-art/bag3_skywater130_workspace` (7 stars, 2024-05), `ucb-art/bag3_ams_cds_ff_mpt`.
- **What it does:** Python generators for schematic, layout, measurement, optimization. C++ `cbag` / `pybag` OA backend. Native **CDL, Spectre, and SystemVerilog** netlisters configured from `netlist_setup/gen_config.yaml` so `BAG_prim` headers match Virtuoso-generated CDL/ADE names. `start_bag.il` in CIW. Docs say BAG3++ "removes dependence on all closed-source CAD programs except the complete Cadence Virtuoso Design Suite."
- **Formats:** OA schematic/layout, CDL, Spectre, SystemVerilog. GDS via Virtuoso stream-out (not verified here).
- **Cadence required:** Yes. Compile C++ against OA. RHEL7-style workspace scripts.
- **PDK abstraction:** `BAG_prim` wrapper cells + process YAML. This is the industrial version of `techmap.json`. New PDK setup is documented as "observe CDF in Virtuoso and replicate."
- **Fit:** If the Linux box already has a BAG workspace for the user's PDK, *run generators there*. Do not try to install BAG on Windows. The idea to steal is `BAG_prim` + `gen_config.yaml`, not the C++ stack.
- **Weaknesses:** Cadence-bound. Heavy. Public workspaces are Sky130 s8 and Cadence `cds_ff_mpt`, not a random foundry PDK. Activity looks academic-maintenance, not weekly. Blue Cheetah commercial fork exists; `bluecheetah/bto` 404'd (private or gone).

---

### 2.4 cicpy / ciccreator (wulffern)

- **cicpy:** https://github.com/wulffern/cicpy (MIT, 22 stars, `pushed_at` 2026-08-30). Alive.
- **ciccreator:** https://github.com/wulffern/ciccreator (GPL-3.0, 44 stars, `pushed_at` 2026-08-08). JSON + SPICE + DRC -> compiled layout (`.cic` / GDS).
- **What cicpy does:** Transpile `.cic` to **Cadence SKILL layout and schematic**, SPICE+CDL, Xschem, Magic, Verilog (experimental), SVG. `sch2mag` / `spi2mag` for open layout.
- **Cadence required:** Only if you load the emitted `.il` in Virtuoso. Transpile itself is standalone.
- **PDK abstraction:** A `*.tech` file (example `sky130.tech`), not CDF names.
- **Fit:** Interesting as a SKILL *schematic emitter* that does not need `spiceIn`. The input is ciccreator's layout-ish JSON, not a designer-friendly analog HDL. Closer to "compiler IR -> many backends" than "OTA in Python."
- **Weaknesses:** Small user base. SKILL schematic quality vs `spiceIn` + PDK pcells is unverified (no Virtuoso here). Minecraft backend tells you the project's sense of humor.

---

### 2.5 OpenAccess Python bindings

Cadence forum (IC design, "Edit schematic with python"): there is no supported Python API inside Virtuoso; OA Python bindings exist elsewhere but "OA doesn't define the semantics for schematics"; use SKILL.

BAG's `cbag` is C++ OA used *with* Virtuoso, not a replacement for it.

**Do not plan a Windows OpenAccess writer.** There is no public, maintained, Virtuoso-safe OA-Python schematic stack to adopt.

---

## 3. Open-source analog PDK and flow ecosystems

### 3.1 Xschem (StefanSchippers)

- **Repo:** https://github.com/StefanSchippers/xschem
- **License:** GitHub field `NOASSERTION` (COPYING fetch 404). Historically GPL; **confirm before vendoring.**
- **Language:** C. 489 stars, `pushed_at` 2026-08-31. The living open analog schematic editor.
- **What it does:** Hierarchical, parametric schematic capture. Four netlist modes: **SPICE, Spectre, Verilog, VHDL**. Batch: `xschem -n -x -s -q -o <dir> top.sch`. LVS mode wraps the top as `.subckt`. Symbols carry `format` / `spectre_format` attributes; a missing `spectre_format` silently drops instances from Spectre netlists (IHP AMS template documents this trap).
- **Cadence required:** No.
- **PDK abstraction:** Per-PDK symbol libraries (`sky130`, `gf180`, `ihp-sg13g2_pr`) with ngspice model names baked into symbols.
- **Can it export a netlist you take into Cadence?** Yes as *text*: SPICE or Spectre. `spiceIn` will instantiate cells whose names exist in the target library. A sky130 Xschem netlist will not bind to `gpdk045/nmos1v` or a TSMC `nch_lvt`. A Spectre-mode Xschem deck can be a starting ADE text testbench only if the Linux PDK's Spectre models use the same device names (true for some university Cadence+sky130 kits; **unverified** for a commercial PDK).
- **Weaknesses:** GUI + Tcl, not a Python IR. Spectre support is "the objects must be known to the target simulator." No CDF.

---

### 3.2 SkyWater sky130, GF180, open_pdks, IHP

| Project | Link | License | Stars | Last push | Role |
|---|---|---|---|---|---|
| skywater-pdk | [google/skywater-pdk](https://github.com/google/skywater-pdk) | Apache-2.0 | 3681 | 2026-07-21 | Open 130 nm PDK umbrella |
| sky130 primitives | [google/skywater-pdk-libs-sky130_fd_pr](https://github.com/google/skywater-pdk-libs-sky130_fd_pr) | Apache-2.0 | 41 | 2024-03-09 | **Archived.** Device models. |
| gf180mcu-pdk | [google/gf180mcu-pdk](https://github.com/google/gf180mcu-pdk) | Apache-2.0 | 524 | **2023-05-31** | Quiet / stale |
| open_pdks | [RTimothyEdwards/open_pdks](https://github.com/RTimothyEdwards/open_pdks) | Apache-2.0 | 446 | 2026-08-27 | Installer: magic / xschem / ngspice / klayout views |
| IHP-Open-PDK | [IHP-GmbH/IHP-Open-PDK](https://github.com/IHP-GmbH/IHP-Open-PDK) | Apache-2.0 | 812 | 2026-09-01 | 130 nm BiCMOS; xschem + ngspice + klayout; CDL/GDS/LEF for *digital* cells |
| IIC-OSIC-TOOLS | [iic-jku/IIC-OSIC-TOOLS](https://github.com/iic-jku/IIC-OSIC-TOOLS) | Apache-2.0 | 1044 | 2026-09-01 | Docker of the open analog+digital stack |

IHP docs describe an open analog flow (xschem / ngspice / klayout). Official Virtuoso libraries are **not** in the public PDK description I fetched. Some symbols include `spectre_format` for Xschem's Spectre/VACASK netlister.

**Export into Cadence:** you can take SPICE/CDL *text*. You cannot take a sky130 `nfet_01v8` instance into a commercial analog lib without a cell/param map. That map is exactly `techmap.json` / `BAG_prim`.

`open_pdks/common/cdl2spi.py` (efabless, 2016-2020) converts CDL *to* SPICE for LVS (unwraps `+` continuations, rewrites diodes/FETs). It is not a Cadence importer.

---

### 3.3 OpenFASOC

- **Repo:** https://github.com/idea-fasoc/OpenFASOC
- **License:** Apache-2.0. 351 stars, `pushed_at` 2025-10-22. Large (420k). Slowing but not dead.
- **What it does:** Spec-to-GDS analog generators on **open** tools (Magic, Netgen, KLayout, Yosys, OpenROAD, open_pdks, Xyce). Generators: temp sensor, LDO, GLayout (sky130/gf180), cryo (in progress).
- **Formats:** Open-tool netlists + GDS. Not Virtuoso OA. Not commercial CDF.
- **Cadence required:** No.
- **Fit:** Parallel universe. A GDS *can* stream into Virtuoso, but that is layout, not a schematic the user can edit as `nch`/`pch` pcells. Device names stay sky130/gf180.
- **Weaknesses:** Open-PDK only. Not a Python circuit IR for arbitrary OTAs.

---

### 3.4 ALIGN

- **Repo:** https://github.com/ALIGN-analoglayout/ALIGN-public
- **License:** BSD-3-Clause. 387 stars, `pushed_at` 2026-08-18. Alive. Sky130 PDK helper: [ALIGN-pdk-sky130](https://github.com/ALIGN-analoglayout/ALIGN-pdk-sky130) (13 stars, 2026-07-06).
- **What it does:** **Consumes** unannotated SPICE + optional `.const.json`, **emits** GDS / JSON layout. Hierarchical annotation, primitive generators, analog PnR. Mock FinFET 14 nm PDK in-tree.
- **Formats:** In: SPICE. Out: GDS, JSON. Not schematic, not CDL, not Spectre.
- **Cadence required:** No. README says output GDS can be imported into Virtuoso or KLayout.
- **Fit:** Downstream of a netlist, not a generator of one. Useful if you later want open layout. Does not solve export-to-schematic.
- **Weaknesses:** Needs a PDK abstraction JSON. Academic-quality layouts. Wrong direction for the current goal.

---

### 3.5 MAGICAL

- **Repo:** https://github.com/MAGICAL-EDA/MAGICAL
- **License:** BSD-3-Clause. C++ / Python. 293 stars, last push **2024-04-24**. Stale academic (UT Austin / IDEA).
- **What it does:** SPICE + symmetry constraints -> placed/routed GDS. Examples: ADC, comparator, OTAs. Sample tech params, not a real PDK.
- **Cadence required:** No.
- **Fit:** Prior art for analog layout, not netlist generation. Treat as dead-for-adoption.

---

### 3.6 CACE (characterization, not generation)

- **Current:** https://github.com/fossi-foundation/cace (Apache-2.0, 13 stars, `pushed_at` 2026-07-17). Older: [efabless/cace](https://github.com/efabless/cace) (58 stars, last 2025-02-07).
- **What it does:** YAML datasheet + xschem/magic/netgen/ngspice -> PVT / Monte Carlo / mismatch characterization. Netlists come *from* Xschem (`--source schematic|rcx`).
- **Fit:** How an open flow *uses* netlists. Not a Python-to-CDL writer. Mentally pair with "LLM reasons about corners" later.

---

### 3.7 KLayout and gdsfactory

- **KLayout:** https://github.com/KLayout/klayout (GPL-3.0, 1184 stars, `pushed_at` 2026-09-01). Layout editor / DRC / LVS. Can extract a SPICE netlist from layout. Not schematic capture.
- **gdsfactory:** https://github.com/gdsfactory/gdsfactory (MIT, 1023 stars, `pushed_at` 2026-09-01). Python layout (photonics + analog experiments). `gplugins` can dump extracted netlists through VLSIR to Spectre/SPICE/Xyce. That is *layout-extracted* connectivity, not a designed schematic.

Neither replaces cdl_gen / Hdl21 for "I typed an OTA."

---

## 4. Netlist format translation

### 4.1 dan-fritchman/Netlist

- **Repo:** https://github.com/dan-fritchman/Netlist
- **License:** BSD-3-Clause. 42 stars. **Last push 2022-12-10.** Stale. Sibling to VLSIR, not the same package.
- **README support table (as published, not re-tested):**

| Dialect | Parse | Write |
|---|---|---|
| Generic SPICE | yes | no |
| HSPICE | yes | no |
| ngspice | no | no |
| CDL | no | no |
| Xyce | no | yes |
| Spectre | yes | no |
| Spectre-SPICE (`simulator lang`) | yes | yes (write example uses SPECTRE) |

- **Honest gap:** The one dedicated "parse many dialects / convert" Python package is abandoned and **does not write CDL**. Do not build on it without a fork and tests.

---

### 4.2 Xyce XDM

- **Repo:** https://github.com/Xyce/XDM
- **License:** GitHub `NOASSERTION`. 25 stars, last push 2024-02-15.
- **What it does:** PSpice / HSPICE / Spectre -> **Xyce**. Mixed C++/Python (`xdm_bdl`). Shipped with Xyce releases.
- **Fit:** Wrong direction (toward Sandia, not toward Cadence). Useful only if you already have a Spectre deck and want Xyce. Not a CDL/`spiceIn` tool.

---

### 4.3 open_pdks `cdl2spi.py`

CDL -> SPICE for LVS. Handles continuation, some FET/diode/$SUB rewrite. Not Spectre. Not CDF. Not Virtuoso.

---

### 4.4 rohaansch/netlist-parser

- **Repo:** https://github.com/rohaansch/netlist-parser
- **License:** MIT. 5 stars, `pushed_at` 2026-04-03.
- **What it claims:** Parse `.spi/.cir/.cdl/.scs/.spf` into cells/ports/devices. CLI summaries.
- **What it is not:** A writer. Tiny. **Unverified** against ADE mixed-lang dumps (this skill's parser is the one that was actually tested on the local corpus).

---

### 4.5 This skill (already here)

`lib/spectre_netlist.py` + `lib/spectre_to_ngspice.py` already parse ADE mixed `simulator lang`, preserve round-trip, and translate toward ngspice with an `unsupported` list (`stb`, `pss`, `xf`, `pz`, `sp`, `montecarlo`). That is the **import / review** half of the user's workflow. There is no CDL writer and no techmap.

Known translation traps (from `ade-netlist-errors.md` / `spectre-syntax.md`, not re-derived):

- `M` mega vs milli (silent 1e9).
- Trailing `A` / `F` in ngspice (atto / femto).
- `include` relative to the netlist directory, not the PDK root.
- `section=` name mismatch.
- Mid-file `simulator lang`.
- Subckt pin order.
- `ahdl_include` / Verilog-A has no ngspice equivalent unless you compile OSDI.

---

### 4.6 CDL vs SPICE vs Spectre (practical)

| | CDL | SPICE (ngspice/HSPICE) | Spectre |
|---|---|---|---|
| Typical use | LVS + `spiceIn` schematic import | Open sim / Xschem | ADE Save netlist |
| Instance typing | Often prefix (`M`, `X`, `C`) | Prefix | Name + master token (`M0 (d g s b) nch`) |
| Params | `W=... L=...` CDF names | Model-card names | CDF / Spectre names |
| `simulator lang` | No | No | Yes |
| Units | SPICE-ish | SPICE milli-`M` | Spectre mega-`M` |
| Travels into Virtuoso schematic | **Yes, via `spiceIn`** | Sometimes (if CDL-shaped) | **No** (ADE *output*, not spiceIn input) |
| Travels into ADE as a text testbench | Poor | If models match | **Yes** |

---

## What a generate-here / simulate-and-export workflow should actually emit

Two different artifacts. Do not merge them.

### Artifact A: local simulation deck (Windows)

- ngspice-safe SPICE, or Spectre-syntax that this skill can already parse.
- Model includes pointing at *this* machine's models (`models/ptm_*.lib` or a copied open PDK), not `$PDK` on Linux.
- No CDF names required.
- This is what PySpice / Hdl21 `fmt="spice"` / `analog_spice` already produce.

### Artifact B: Cadence import bundle (the thing you scp to Linux)

Minimum files:

1. **`design.cdl`**: `.SUBCKT` / `.ENDS`, instance lines `{inst} {nodes...} {pdk_cell} {cdf}={val}`, pin order matching the intended symbol.
2. **`techmap.json`** (or equivalent): PDK lib name, cell map (`nmos` -> `nch_lvt_mac`), param map (`w_f` -> `fw` or `w`), defaults, optional `w_tot` policy.
3. **`design.json` placement** (optional but required for a readable schematic): instance xy, pins, rails, diode shorts. `spiceIn` alone is not enough; cdl_gen's README is explicit.
4. **`reflib` list**: PDK libraries `spiceIn` must search.
5. **Sidecar metadata** (JSON): intended corner/section names, global nets (`vdd!`), hierarchical cell list, terminal order convention, unit convention (`1u` not `1e-6A`).

Optional third file if you only want ADE to *simulate* a text deck: `design.scs` in Spectre syntax with `simulator lang=spectre`, `include` of the *Linux* model path, and `section=tt`. That is Artifact C, not a substitute for B.

### Which format travels best into Cadence

- **Schematic import:** CDL (or SPICE that is CDL-shaped). This is what `spiceIn` is for. cdl_gen, BAG's CDL netlister, and cicpy `--spice` all bet on this.
- **Simulation of an existing Virtuoso testbench:** you do not emit a schematic at all; you emit knobs and let ADE netlist. The reverse (pull `input.scs`) is virtuoso-bridge Maestro snapshot or a manual copy. This skill already digests that file.
- **Spectre text as the *only* export:** works for "run this deck in ADE command line" if models and units are Spectre. Fails as a schematic handoff.

### Metadata that must ride along

- PDK library and primitive cell names (not `nmos` / `pch`).
- CDF parameter *names* and the meaning of `w` (finger vs total).
- Finger count vs `m` multiplier (some PDKs use both; they are not the same).
- Terminal order.
- Subckt pin order and pin directions (spiceIn pins are often `inputOutput`).
- Technology library binding (`techBindTechFile`); cdl_gen `createlib` does this because unbound libs hide CDF.
- Corner / `section` names for any model include you also ship.
- Whether totals are written (`w_tot`) or left for CDF callbacks (`cdlgenSetFingers`).

### Specific pitfalls

1. **Import-time Cadence checks** (cdl_gen) make the writer unusable on Windows. Split packages.
2. **`w` ambiguity.** Always map by CDF prompt from `cdlgenDumpCDF` or a BAG-style dump, never by English.
3. **Units.** Emit Spectre decks with Spectre units; emit CDL with foundry-expected suffixes (`1u`). Never emit `0.0001A`.
4. **Port order.** Document `[D,G,S,B]` vs `[G,D,S,B]` in the sidecar. A converter that "fixes" order without a flag will invert MOSFETs.
5. **`simulator lang`.** A CDL file must not contain Spectre-only statements. A `.scs` file must declare lang before mixed cards.
6. **Open-PDK names in a commercial library.** `sky130_fd_pr__nfet_01v8` is not a Virtuoso pcell in a TSMC workspace.
7. **Callbacks.** After spiceIn, pcell parameters may be stale until a SKILL callback runs.
8. **Placement vs connectivity.** Two files. Connectivity wins for LVS; placement wins for humans. Keep sizes in the Python/CDL, not in the JSON (cdl_gen's rule).
9. **License.** PySpice GPL, InSpice AGPL, Xschem likely GPL, KLayout GPL. A BSD/MIT IR (Hdl21, cdl_gen, VLSIR) is the safer core.

---

## Gaps nobody fills well

1. **No Cadence-free CDL writer that also knows commercial CDF names.** cdl_gen has the right map and the wrong import-time dependency. Hdl21 has the right standalone IR and no CDL/CDF backend. BAG has CDL+CDF and requires Virtuoso. This is the actual product to build: `techmap` + CDL emit, no `spiceIn` on the writer.

2. **No living spectre <-> CDL translator.** Netlist (2022) does not write CDL. XDM writes Xyce. `cdl2spi.py` is LVS-oriented CDL->SPICE. This skill translates Spectre->ngspice, not Spectre->CDL. ADE `input.scs` is a *configured testbench*, not a schematic source; going the other way (scs -> spiceIn) loses analyses, `save`, and lang switches, and still needs CDF names.

3. **No PDK-agnostic analog HDL with a TSMC/Samsung/gPDK plugin.** Hdl21 plugins stop at open PDKs. cdl_gen's techmap is a JSON file you fill by hand from the CIW. BAG's `BAG_prim` is the only complete commercial answer and it is not portable to Windows.

4. **Schematic placement after netlist import is unsolved except as a per-circuit JSON.** spiceIn will always look like a ratsnest. cdl_gen placement is manual. BAG generates from templates. Nobody has a reliable auto-place for analog that an LLM can trust. Plan on LLM-written placement JSON plus `extractsch` after the first human tidy.

5. **Open analog flows do not export commercial-Cadence netlists.** Xschem/OpenFASOC/ALIGN/MAGICAL/CACE are a complete *other* toolchain. GDS import is not schematic import. Using them only makes sense if the Linux PDK is also sky130/gf180/IHP.

6. **OA schematic semantics are not a public Python API.** skillbridge / virtuoso-bridge / BAG SKILL are the supported doors. A "write `sch.oa` on Windows" plan will fail.

7. **LLM-oriented analog design tools are either PCB (SKiDL skills) or Cadence-session agents (virtuoso-bridge).** Nobody ships "reason about gm/ID, emit CDL+techmap, file a schematic on the other machine" as one library. That is the gap this skill is in a position to fill on the Windows side, with cdl_gen's Linux half copied or wrapped.

8. **SKiDL / schemdraw / Lcapy / CircuitPython look like prior art in a web search and are not.** Filtering those names saves months.

---

## Verification appendix (commands actually run)

GitHub API, 2026-09-01, via `urllib.request` to `https://api.github.com/repos/{owner}/{repo}`. Observed fields (truncated):

```
KwantaeKim/cdl_gen                  stars=31   lic=MIT          pushed=2026-08-25  archived=False
dan-fritchman/Hdl21                 stars=96   lic=BSD-3-Clause pushed=2026-02-17  archived=False
Vlsir/Vlsir                         stars=40   lic=BSD-3-Clause pushed=2026-02-15  archived=False
devbisme/skidl                      stars=1640 lic=MIT          pushed=2026-08-20  archived=False
PySpice-org/PySpice                 stars=863  lic=GPL-3.0      pushed=2026-09-01  archived=False
mph-/lcapy                          stars=300  lic=LGPL-2.1     pushed=2026-08-16  archived=False
cdelker/schemdraw                   stars=264  lic=MIT          pushed=2026-07-18  archived=False
unihd-cag/skillbridge               stars=340  lic=LGPL-3.0     pushed=2026-03-23  archived=False
Arcadia-1/virtuoso-bridge-lite      stars=692  lic=MIT          pushed=2026-08-30  archived=False
DavidQyz/virtuoso-bridge-lite       stars=0    lic=MIT          pushed=2026-06-12  archived=False
ucb-art/BAG_framework               stars=172  lic=BSD-3-Clause pushed=2022-12-04  archived=False
ucb-art/bag                         stars=39   lic=NOASSERTION  pushed=2024-12-27  archived=False
wulffern/cicpy                      stars=22   lic=MIT          pushed=2026-08-30  archived=False
StefanSchippers/xschem              stars=489  lic=NOASSERTION  pushed=2026-08-31  archived=False
idea-fasoc/OpenFASOC                stars=351  lic=Apache-2.0   pushed=2025-10-22  archived=False
ALIGN-analoglayout/ALIGN-public     stars=387  lic=BSD-3-Clause pushed=2026-08-18  archived=False
MAGICAL-EDA/MAGICAL                 stars=293  lic=BSD-3-Clause pushed=2024-04-24  archived=False
dan-fritchman/Netlist               stars=42   lic=BSD-3-Clause pushed=2022-12-10  archived=False
Xyce/XDM                            stars=25   lic=NOASSERTION  pushed=2024-02-15  archived=False
fossi-foundation/cace               stars=13   lic=Apache-2.0   pushed=2026-07-17  archived=False
IHP-GmbH/IHP-Open-PDK               stars=812  lic=Apache-2.0   pushed=2026-09-01  archived=False
```

Source files read (not cloned):

- `KwantaeKim/cdl_gen`: README.md, `__init__.py`, `device.py`, `subckt.py`, `virtuoso.py` (partial), `techmap.json`
- `dan-fritchman/Hdl21`: `readme.md`
- `Vlsir/Vlsir`: `readme.md`, `VlsirTools/readme.md`
- `unihd-cag/skillbridge`: README.md
- `Arcadia-1/virtuoso-bridge-lite`: README.md
- `ALIGN-analoglayout/ALIGN-public`: README.md
- `MAGICAL-EDA/MAGICAL`: README.md
- `wulffern/cicpy`: README.md
- `idea-fasoc/OpenFASOC`: README.rst (via API)
- Local: `SKILL.md`, `references/ade-netlist-errors.md`, `references/spectre-syntax.md`, `lib/spectre_to_ngspice.py` header

**Not tested:** any `spiceIn`, Virtuoso, Spectre, Xschem batch netlist, Hdl21 `h.netlist`, BAG generate, ALIGN `schematic2layout`, or a real PDK techmap other than reading `cdl_gen`'s gpdk045 JSON. Cadence-side claims remain **UNVERIFIED** on this host.

**Top ways this note could still be wrong:**

- virtuoso-bridge-lite's "import CDL via spiceIn" is from the project's own comparison table, not a run I performed.
- Hdl21 may have grown an unpublished CDL writer after 7.0.0; it is not in the README I fetched.
- Xschem license is not confirmed from a LICENSE file.
- BAG3++ public activity may understate internal BWRC / Blue Cheetah work.
- A private university Cadence+sky130 kit would change the "open netlists cannot enter Cadence" line for that one PDK only.
- GitHub star/push snapshots move; treat dates as 2026-09-01.
