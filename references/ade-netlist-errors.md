# ADE / Spectre netlist errors

Catalogue of faults that actually break Cadence ADE "Save netlist" / Spectre `.scs` files, plus the ngspice wording when the same fault is fed to ngspice (this machine has ngspice, not Spectre).

**Verification status:** There is **no Spectre binary** here. Spectre symptom strings marked **FORUM** are copied from public Cadence Community / edaboard logs. Strings marked **RECONSTRUCTED** follow documented `SFE-*` / `SPECTRE-*` codes and typical Spectre log shape but were not captured from a live `spectre` run. ngspice strings marked **NGSPICE-RUN** were produced on this machine. Do not claim Spectre verification.

Each entry: symptom, root cause, mechanical netlist check, fix.

---

## 1. Missing or wrong `include` path

**Corpus:** `08_broken_missing_include.scs`

**Symptom (Spectre, RECONSTRUCTED):**

```
Error found by spectre during circuit read-in.
ERROR (SFE-24): "input.scs" 9: Unable to open the file
`$PDK/models/spectre/this_file_does_not_exist.scs'.
```

The exact SFE number for "file not found" varies by release. Related **FORUM** code for a *recursive* include:

```
FATAL (SFE-879): Recursive file include or library call: `.../netlist/input.scs'.
```

**Symptom (ngspice-44.2, NGSPICE-RUN, `ngspice_con.exe -b`):**

```
Error: Could not find include file no_such_file_xyz.scs
    While reading .../missing_include.cir
ERROR: fatal error in ngspice, exit(1)
```

**Root cause:** ADE Setup -> Model Libraries points at a path that does not exist on this host, `$PDK` / `$PROJECT_SCS_FILE` is unset, or the relative path is resolved against the netlist directory (usually `simulation/<cell>/spectre/schematic/netlist/`) instead of the PDK root.

**Detect:** For every `include "path"` / `.include` / `ahdl_include`:
1. Expand `~` and `$VAR`.
2. If the result is not an absolute path, join it to the including file's directory.
3. Fail if the file does not exist.
4. Also fail if `include "input.scs"` points at the netlist itself (SFE-879).

**Fix:** Point Model Libraries at the PDK's real `.scs` / `.lib`. Export `PDK`. Do not add `input.scs` as a model file.

---

## 2. Missing or wrong `section=` corner

**Corpus:** `08_broken_missing_section.scs`

**Symptom (Spectre, FORUM):**

```
Error found by spectre during circuit read-in.
ERROR: "input.scs" 14: The file `/p/tech1/.../include.scs' appears to be in
Spectre syntax, but Spectre cannot find the `section tttt' library defined in it.
Ensure that the `tttt' section is defined by `section/endsection' block and
rerun the simulation.
```

**Root cause:** ADE corner / Model Libraries "section" field does not match a `section <name>` ... `endsection` (or `library` section) in the included file. Common typos: `tttt` vs `tt`, `tt_ll` vs `tt`, empty section on a file that is *not* sectional.

**Detect:**
1. Collect every `include "file" section=NAME`.
2. Open `file` (after path checks).
3. Accept if you find `section NAME` / `endsection` or a `.lib` section of that name.
4. If the file has **no** sections, `section=` is itself the bug (include the whole file).
5. If the file has sections and `NAME` is absent, list the defined names.

**Fix:** Align ADE Model Libraries / Assembler corner with the PDK section list (`tt`, `ss`, `ff`, ...). Drop `section=` when the file is a flat include.

---

## 3. `simulator lang` left in the wrong mode

**Corpus:** `08_broken_wrong_lang.scs`

**Symptom (Spectre, FORUM):**

```
ERROR (SFE-1802): ".../cell.schematic.spectre.netlist" 5:
Spectre "subckt" statements are not supported in spice language sections.
Use "simulator lang = spectre" to introduce Spectre language sections.
ERROR (SFE-1802): ".../cell.schematic.spectre.netlist" 6:
Spectre "parameters" statements are not supported in spice language sections.
```

Related **FORUM** (included file without `.scs` and without a lang line):

```
ERROR (SFE-1025): Instance `Ibc1614': Unexpected value `wli<16>'
- all required positional parameters have already been specified.
```

**Root cause:** Spectre starts included non-`.scs` files in SPICE mode. A Spectre-native dump (`subckt`, `parameters`, `name (nodes) master`) is then parsed as SPICE. First-letter typing and `+` continuation eat node names. The same class of bug happens *inside* one file if you switch to `simulator lang=spice` and forget to switch back.

**Detect:** Walk the file with a lang flag (`.scs` starts Spectre; other starts SPICE). After each `simulator lang=...`, check:
- In SPICE mode: reject bare `subckt` / `parameters` / `model name bsim4` / `include` without a leading dot, and reject `(node lists)` on instances.
- In Spectre mode: reject `.model` / `.subckt` / `.dc` unless you are being unusually generous.

**Fix:** Put `simulator lang=spectre` at the top of every Spectre-language include, or rename it to `.scs`. After a SPICE `.model` block, write `simulator lang=spectre` before the next Spectre statement.

---

## 4. Floating / undriven node

**Corpus:** `08_broken_floating_node.scs`

**Symptom (Spectre, RECONSTRUCTED):**

```
WARNING: No DC path from node `float_n' to ground.
```

**Symptom (ngspice-44.2, NGSPICE-RUN) for a capacitor-only node:**

```
Warning: singular matrix:  check node float_n
Note: Starting dynamic gmin stepping
Warning: Dynamic gmin stepping failed
Note: Starting true gmin stepping
Warning: True gmin stepping failed
Note: Starting source stepping
Warning: source stepping failed
Note: Transient op started
Note: Transient op finished successfully
```

ngspice may still print a `0 V` result after the transient-op fallback. Do not treat a finished `.op` as proof the node is driven.

or, once the Spectre DC matrix is singular:

```
Matrix is singular (detected at `float_n').
Trying `homotopy = dptran' ...
ERROR (SPECTRE-16080): Cannot print DC solution because DC did not converge.
```

**FORUM** (singular matrix during IC / tran, same family):

```
Matrix is singular (detected at `I0.fil').
Trying `homotopy = dptran' for initial conditions.
ERROR (SPECTRE-16080): Cannot print DC solution because DC did not converge.
```

**Root cause:** A node is connected only through capacitors, or is a dangling schematic pin / unconnected wire. No DC path to `0` / `global 0`. Spectre often *warns* and inserts `gmin`; ideal C-only nodes still blow up homotopy.

**Detect:** Build the undirected graph of devices that conduct at DC (resistor, MOSFET channel, vsource, isource, inductor). Ignore capacitors. Every named node except pure capacitor plates must reach `0`. Flag nodes with degree 0 or only-C connections.

**Fix:** Tie the node through a large resistor to ground, connect the missing wire, or set `cmin` / `gmin` only as a last resort after the schematic is actually connected.

---

## 5. Illegal character in a node name (current language mode)

**Corpus:** `08_broken_illegal_node.scs`

**Symptom (Spectre, RECONSTRUCTED / related FORUM):**

```
ERROR (SFE-1025): Instance `Csniff': Unexpected value `vout=fb'
- all required positional parameters have already been specified.
```

Related **FORUM** when a bus name is parsed in SPICE mode:

```
ERROR (SFE-1025): Instance `Ibc1614': Unexpected value `wli<16>'
- all required positional parameters have already been specified.
```

**Symptom (ngspice-44.2, NGSPICE-RUN) for a node token `vout=fb`:**

```
Netlist line no. 2:
Undefined parameter [fb]
Netlist line no. 2:
Cannot compute substitute
ERROR: fatal error in ngspice, exit(1)
```

`#` is **not** illegal in ngspice: `vout#fb` was accepted as a node name and then failed as a floating capacitor (`Warning: singular matrix:  check node vout#fb`). `net<0>` was also accepted. The corpus uses `=` because that character is a parameter delimiter in both languages.

**Root cause:** The name contains a character the active language uses for syntax (`=`, SPICE leading `+`, Spectre/CPP `#`, unquoted spaces). Virtuoso bus names with `<` `>` are legal in Spectre and illegal or mis-tokenized in SPICE mode.

**Detect:**
- Spectre mode: flag `#`, whitespace, and raw `=` inside a node token.
- SPICE mode: flag leading `+`, `*`, and treat `<` `>` bus notation as high-risk.
- Compare node tokens to the Spectre reserved-word list if they are used as instance names.

**Fix:** Rename the net in the schematic. For buses, keep Spectre mode (`.scs` or `simulator lang=spectre`) so `net<0>` stays one name.

---

## 6. Subckt port-count mismatch

**Corpus:** `08_broken_port_mismatch.scs`

**Symptom (Spectre, RECONSTRUCTED):**

```
ERROR: "input.scs" 18: Instance `I0' of `cs_amp' has 3 terminals,
but the subcircuit definition has 4.
```

**Symptom (ngspice-44.2, NGSPICE-RUN):**

```
Too few parameters for subcircuit type "amp" (instance: xx1)
    Simulation interrupted due to error!
```

(`X1` is canonicalized to `xx1`.) The definition can also be skipped first if a lang error hid the `.subckt`.

**Root cause:** Instance node list does not match `subckt` port list. Usual ADE sources: pin order changed on the symbol, a pin added on the cell but not on the symbol, or a netlister dropped `vss` because it thought it was global.

**Detect:** Map each `subckt` name to `len(ports)`. For each instance whose master is that name, require `len(nodes) == len(ports)`.

**Fix:** Align symbol pins with the cellview pin order. Re-netlist. Do not "fix" it by adding a dummy node in the `.scs` unless you own that file.

---

## 7. Duplicate instance name

**Corpus:** `08_broken_duplicate_instance.scs`

**Symptom (Spectre, RECONSTRUCTED):**

```
ERROR: "input.scs" 14: Duplicate instance name `M0'.
```

**Symptom (ngspice-44.2, NGSPICE-RUN):**

```
Error on line 3 or its substitute:
  r1 1 0 2k
device already exists, bail out
    Simulation interrupted due to error!
```

**Root cause:** Two instances share a name at the same hierarchy (copy-paste in a hand-edit, or two schematic instances forced to the same name). Nested `if` / `else` can legally reuse a name only under Spectre's strict structural-if rules; ADE schematic dumps should never rely on that.

**Detect:** At each scope (top, each subckt), count instance names. Any name with count > 1 is a fault. Do not flag the same name in different subckts.

**Fix:** Rename one instance. In Virtuoso, check Instance Name uniqueness before Save netlist.

---

## 8. Instance references a model / subckt that is never defined

**Corpus:** `08_broken_undefined_model.scs`

**Symptom (Spectre, FORUM):**

```
ERROR (SFE-23): "input.scs" 13: The instance `MP10' is referencing an
undefined model or subcircuit, `modp'. Either include the file containing
the definition of `modp', or define `modp' before running the simulation.
```

Shorter **FORUM** form:

```
ERROR (SFE-23): "input.scs" 17: c1 is an instance of an undefined model cmodel.
```

**Symptom (ngspice-44.2, NGSPICE-RUN):**

```
warning, can't find model 'nch_missing' from line
    m0 2 1 0 0 nch_missing w=2u l=180n
Error on line 2 or its substitute:
  m0 2 1 0 0 nch_missing w=2u l=180n
could not find a valid modelname
    Simulation interrupted due to error!
```

**Root cause:** PDK models were not included, the wrong corner section was included (so `nch` never appears), extracted parasitics reference `met1` / `cmodel` that QRC was told to emit as models, or a typo (`nch_missing`).

**Detect:** Collect masters from every instance. A master is defined if it is (a) a Spectre built-in primitive, (b) a `model` / `.model` name, (c) a `subckt` / `.subckt` name, or (d) a Verilog-A module from a successful `ahdl_include`. Anything else is undefined. Built-in list at minimum: `resistor`, `capacitor`, `inductor`, `vsource`, `isource`, `vcvs`, `vccs`, `ccvs`, `cccs`, `iprobe`, `port`, `options`, `bsource`.

**Fix:** Include the PDK model file with the correct `section=`. For QRC, set parasitic R/C models to "Include as comment" / "Do not include" so they netlist as `resistor` / `capacitor`.

---

## 9. `M` vs `m` unit ambiguity (silent 1e9)

**Corpus:** `08_broken_unit_Mm.scs`

**Symptom:** Usually **no error**. The circuit "simulates" with a 1 milliohm load instead of 1 megohm (or the reverse). Gain, current, and time constants are off by 1e9.

**ngspice-44.2 NGSPICE-RUN** (same level-1 MOSFET, `RD=1M` vs `RD=1meg`):

| Netlist | `RD` resistance printed by ngspice | `v(vout)` |
|---|---:|---:|
| `RD vdd vout 1M` | `0.001` | `1.800000e+00` (load is a short) |
| `RD vdd vout 1meg` | `1e+06` | `4.082481e-03` |

That is a factor of 1e9 on the resistor, and the DC operating point is completely different.

**Root cause:** Spectre SI suffixes are case-sensitive: `M` = 1e6, `m` = 1e-3. SPICE / ngspice / HSPICE treat `M` and `m` as milli; mega is `meg`. ADE files that switch to `simulator lang=spice` (or are replayed in ngspice) reinterpret every `1M`.

**Detect:**
1. Track language mode per statement.
2. In SPICE mode, flag any literal matching `[0-9.]+M` (mega intended) and any `meg` that will be read back in Spectre mode.
3. In Spectre mode, flag `1m` on resistors that look like they wanted mega (heuristic: `r=1m` on a load resistor is often a mistake).
4. Never auto-convert; report both interpretations.

**Fix:** Write Spectre mega as `1M` only in Spectre mode. Write SPICE mega as `1meg`. Prefer `1e6` / `1e-3` when the file will be read by both.

---

## 10. Missing `save` so the wanted signal is not in the results

**Corpus:** `08_broken_missing_save.scs`

**Symptom:** Simulation "succeeds". ADE plot / `results()` cannot find `vout`. With `save=selected` and **no** voltage `save` lines, modern Spectre may silently fall back to `allpub` (so you think you selected nothing and still get everything), or it stores no public voltages.

Cadence documented fallback (forum, Spectre 21.1 ISR7+): `save=selected` with no saved node voltages becomes `allpub` unless `saveselectedtoallpub=nooutput`.

**Root cause:** Outputs pane empty, `saveOptions options save=selected`, and no `save vout` lines. Or you saved only currents.

**Detect:**
1. Find `saveOptions` / `options save=`.
2. If `save=selected` (or missing, which defaults to selected in some APS versions), collect `save` statements.
3. If the user's requested nets are not in that list, flag missing save.
4. If the list has only `*:currents` / terminal saves and no node voltages, flag the allpub fallback.

**Fix:** Add `save vout` (or ADE Outputs -> To Be Saved). Or set `save=allpub` when you really want everything.

---

## 11. DC convergence failure

**Corpus:** none (needs a live analog solve; do not fake a "guaranteed singular" circuit).

**Symptom (Spectre, FORUM):**

```
Error found by spectre during IC analysis, during transient analysis `tran'.
ERROR (SPECTRE-16080): Cannot print DC solution because DC did not converge.
Failed test: | Value | > RelTol*Ref + AbsTol
Top 10 Solution too large
...
ERROR (SPECTRE-16192): No convergence achieved with the minimum time step specified.
WARNING (SPECTRE-16266): Error requirements were not satisfied because of
convergence difficulties.
```

**Root cause (in practice, in this order):**
1. Floating / shorted / looped voltage sources (connectivity).
2. Bad Verilog-A (discontinuous `abs`, un-limitted `exp`, missing `$limit`).
3. Ideal switches, ideal diodes, or huge `M`/`m` unit mistakes.
4. Only then: tight `reltol` / missing homotopy.

**Detect (static, mechanical):**
1. Run the floating-node check (entry 4).
2. Flag parallel `vsource` instances across the same node pair with different `dc`.
3. Flag `ahdl_include` modules that use `abs()`, raw `exp()`, or `$bound_step` without `$limit`.
4. Flag `r=0` or `1M` in SPICE mode on supplies.
5. You cannot prove convergence without a solver.

**Standard remedies (Spectre):**
1. Fix the schematic / model first.
2. `homotopy=all` on the DC analysis (gmin + source + dptran + ptran).
3. `gmin=1e-10` to `1e-12`, `cmin` for true C-only nodes.
4. `readns` / `write` a previous `spectre.dc` as nodeset.
5. Loosen `reltol` only after 1-4. Do not start there.

ngspice equivalents: `.options gmin=...`, source stepping, `rshunt`.

---

## 12. Analysis references a source or node that does not exist

**Corpus:** `08_broken_analysis_bad_ref.scs`

**Symptom (Spectre, RECONSTRUCTED):**

```
ERROR: "input.scs" 18: Analysis `dc1': device `VGS' is not defined.
ERROR: "input.scs" 19: Analysis `noise1': oprobe `Vmissing' is not defined.
```

**Symptom (ngspice-44.2, NGSPICE-RUN):**

```
Fatal error: DC Transfer Function: Voltage source, current source, or resistor named "vgs" is not in the circuit
doAnalyses: no such device
run simulation(s) aborted
```

**Root cause:** DC sweep `dev=` / noise `oprobe=` / `iprobe=` / `stb probe=` / `xf probe=` names an instance that was renamed, never netlisted, or lives inside a subckt and was referenced without hierarchy.

**Detect:** After collecting instance names at all scopes, check analysis params `dev`, `mod`, `oprobe`, `iprobe`, `probe`, `portv`, `porti`. Each value must be an instance name (or `inst.term` that resolves). Hierarchical probes need `I0.VIN` not `VIN` if `VIN` is inside `I0`.

**Fix:** Point the analysis at a real top-level source or add an `iprobe` in the schematic and re-netlist.

---

## 13. `ahdl_include` of a Verilog-A file that is absent

**Corpus:** `08_broken_missing_ahdl.scs`

**Symptom (Spectre, RECONSTRUCTED for the missing file):**

```
ERROR: "input.scs" 9: Unable to open Verilog-A file
`$PDK/veriloga/missing_resistor.va' specified by ahdl_include.
```

If the file exists but CMI compile fails (**FORUM**):

```
ERROR (VACOMP-1008): Cannot compile ahdlcmi module library.
Check the log file input.ahdlSimDB/.../ahdlcmi.out for details.
```

**Root cause:** PDK Verilog-A path wrong / `$PDK` unset, or `ahdlcmi` cannot compile (32-bit vs 64-bit gcc, missing libc). A missing file is the netlist fault; VACOMP-1008 is an environment fault.

**Detect:** Same existence check as `include`, applied to every `ahdl_include "..."`. Then require a `module <name>` in that file for each instance master that is not otherwise defined.

**Fix:** Correct the path. For VACOMP-1008, run 64-bit Spectre (`-64`) and use the gcc Cadence ships. Do not symlink in a random system gcc as the first move.

---

## 14. Extra faults worth checking (no dedicated corpus file)

### 14.1 Recursive include of `input.scs`

**FORUM:** `FATAL (SFE-879): Recursive file include or library call`.

**Detect:** The include graph must be a DAG; `input.scs` must not appear in Model Libraries.

### 14.2 Encrypted / missing PDK after copy

Symptom is still SFE-23 or "unable to open file". Detect: `include` path exists but is 0 bytes, or is a `.ctl` / encrypted wrapper without a license.

### 14.3 `global 0` missing while node `0` is used

Usually tolerated (Spectre treats `0` as ground). Still flag schematic nets named `gnd!` that were not declared `global` and never tied to `0`.

---

## Mechanical checker checklist

For a static pass over `input.scs` (no Spectre required):

1. Resolve every `include` / `ahdl_include` path; record missing files.
2. For each `section=`, require that section to exist in the target.
3. Track `simulator lang`; flag Spectre keywords in SPICE mode and the reverse.
4. Count `subckt` ports vs instance nodes.
5. Unique instance names per scope.
6. Every instance master is primitive, model, subckt, or VA module.
7. DC-connectivity / floating-node graph.
8. Analysis `dev` / `probe` / `oprobe` / `iprobe` names resolve.
9. Suffix audit: `1M` / `1m` / `1meg` vs current lang.
10. `save` vs `saveOptions` vs requested outputs.

None of these checks replace a Spectre run. They catch the faults that waste the run.
