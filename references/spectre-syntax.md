# Spectre netlist syntax (lookup)

A compact reader reference for Cadence Spectre / ADE "Save netlist" output (`.scs`, `input.scs`).

**Verification status:** No Spectre binary is installed on this machine. Forms below are taken from the Spectre Circuit Simulator Reference, Ken Kundert's Designer's Guide Appendix B, and Cadence forum ADE dumps. They have **not** been executed against Spectre. Do not treat this file as a substitute for `spectre -h <topic>`.

ADE dumps mix Spectre-native and SPICE-compatible syntax in one file. A parser must track `simulator lang` and apply the matching comment, continuation, number, and statement rules.

---

## 1. File shape (ADE Save netlist)

Typical top-to-bottom order:

1. Header comments: `// Generated for: spectre`, library / cell / view.
2. `simulator lang=spectre`
3. `global 0` (sometimes also `vdd!` and other globals)
4. `parameters ...`
5. `include "..."` and `ahdl_include "..."`
6. `subckt` definitions (one per schematic cell), then top-level instances
7. `simulatorOptions options ...`
8. Analyses (`dcOp dc ...`, `ac1 ac ...`, ...)
9. `save ...` lines
10. `saveOptions options save=allpub` (or `save=selected`)

`.scs` files start in Spectre mode. Other suffixes start in SPICE mode unless the first statement switches. An `include` of a non-`.scs` file **starts the included file in SPICE mode** and restores the caller's mode after the file ends. That is why every Spectre-language include should begin with `simulator lang=spectre` or use a `.scs` suffix.

---

## 2. Statement forms (Spectre mode)

Three primary statement kinds. Names are **not** typed by the first letter (unlike SPICE).

| Kind | Form | Example |
|---|---|---|
| Instance | `name (n1 n2 ...) master p=v ...` | `M0 (vout vin 0 0) nch w=2u l=180n` |
| Instance (no parens) | `name n1 n2 ... master p=v ...` | `M0 vout vin 0 0 nch w=2u l=180n` |
| Model (inline) | `model name master p=v ...` | `model nch bsim4 type=n toxe=1.85n` |
| Model (brace) | `model name master { p=v ... }` | see section 8 |
| Analysis | `name type p=v ...` | `ac1 ac start=1 stop=1G dec=10` |
| Subckt | `subckt name p1 p2 ...` ... `ends name` | see section 7 |

**No-parens split rule** (needed by readers): the last bare token before the first `name=value` pair is the master; tokens between the instance name and that master are nodes.

Parentheses around the node list are optional. ADE almost always emits them.

Masters are a built-in primitive (`resistor`, `vsource`, `capacitor`, `iprobe`, `options`, ...), a `model` name, a `subckt` name, or a Verilog-A module name.

---

## 3. Comment forms

| Mode | Form | Notes |
|---|---|---|
| Spectre | `//` to end of line | Most ADE lines |
| Spectre | `/* ... */` | May span lines |
| SPICE | `*` at start of line | Whole-line comment |
| SPICE | `$` trailing | HSPICE-style end-of-line comment |
| SPICE | `;` trailing | Also seen on SPICE cards |

A line that is only a comment is not a statement. ADE headers are `//` comments **before** `simulator lang=spectre`; those comments are still Spectre-style because `.scs` starts in Spectre mode.

---

## 4. Continuation rules

Both appear in ADE output. Join physical lines into one logical statement before tokenizing.

| Style | Rule | Typical source |
|---|---|---|
| Trailing `\` | If a line ends with `\`, the next physical line continues it. The `\` is not part of the statement. | ADE `simulatorOptions`, long MOSFET params |
| Leading `+` | If a line starts with `+` (SPICE column-1 convention), it continues the previous logical statement. The `+` is not a token. | Foundry SPICE models, some ADE dumps |

Cannot continue across a file boundary (`include`).

---

## 5. `simulator lang`

```
simulator lang=spectre
simulator lang=spice
```

| Effect | Spectre mode | SPICE mode |
|---|---|---|
| Case | Case-sensitive; keywords are lowercase | Case-insensitive (SPICE tradition) |
| Comments | `//` and `/* */` | `*`, `$`, `;` |
| Instances | `name (nodes) master p=v` | First-letter typing: `R`, `C`, `L`, `V`, `I`, `M`, `X`, ... |
| Models | `model name master ...` | `.model name type ...` |
| Subckt | `subckt` / `ends` | `.subckt` / `.ends` |
| Params | `parameters a=1 b=2` | `.param a=1` |
| Include | `include "file"` | `.include` / `.lib` |
| Analyses | `ac1 ac start=...` | `.ac`, `.dc`, `.tran` |
| Scale factors | SI / Spectre table (section 10) | SPICE table (section 10) |

Leaving the language in the wrong mode is a first-class ADE bug. Spectre-native words (`subckt`, `parameters`, `model nch bsim4`) are **not** valid in a SPICE section.

---

## 6. Include, library, ahdl

```
include "filename"
include "filename" section=sectionName
ahdl_include "module.va"
```

SPICE-mode equivalents: `.include "file"`, `.lib "file" section`.

| Piece | Meaning |
|---|---|
| `"filename"` | Relative to the including file, then `-I` search path. `~` and `$ENV` expand on Spectre `include`. |
| `section=` | Include only that `section` / `endsection` (or `library` / `endlibrary`) block. Used for process corners (`tt`, `ss`, `ff`). |
| `.scs` suffix | Included file is read in Spectre mode even without a lang line. |
| other suffix | Included file starts in SPICE mode. |
| `ahdl_include` | Pull in a Verilog-A source. The module name becomes a master. |

Library definition form (usually in the PDK, not in `input.scs`):

```
library corner_lib
section tt
    model nch bsim4 type=n ...
endsection
endlibrary
```

---

## 7. Subckt

ADE form (ports are bare names, parameters on a following line):

```
subckt cs_amp vin vout vdd vss
parameters W=2u L=180n
    M0 (vout vin vss vss) nch w=W l=L
    RD (vdd vout) resistor r=10k
ends cs_amp

I0 (net1 net3 vdd 0) cs_amp
```

Alternate forms a reader must accept:

```
subckt cs_amp (vin vout vdd vss)
subckt cs_amp vin vout vdd vss / parameters W=2u L=180n
```

Nested `subckt` is legal. Port order on the instance must match the definition. Instance names need not start with `X` (ADE uses `I0`, `I1`, ...).

`inline subckt` exists (PDK model wrapping). Not used in this skill's corpus.

---

## 8. Model

```
model nch bsim4 type=n toxe=1.85n vth0=0.32
model nch bsim4 {
    type=n
    version=4.5
    toxe=1.85n
}
```

Brace blocks may span many lines. Nested `{ }` appear in some PDKs (conditional / binning). Tokenize with brace depth; do not use a single greedy regex.

SPICE-mode:

```
.model nch nmos level=54 version=4.5 toxe=1.85e-9
+ vth0=0.32 vsat=1.0e5
```

---

## 9. Analyses, alter, sweep

Spectre form: `name type params`. ADE names the analysis (`dcOp`, `ac1`).

| Type | Typical ADE line | Role |
|---|---|---|
| `dc` | `dcOp dc write="spectre.dc" maxiters=150 annotate=status` | Operating point |
| `dc` | `dc1 dc dev=VIN param=dc start=0 stop=1.8 step=0.01` | DC sweep |
| `ac` | `ac1 ac start=1 stop=1G dec=10 annotate=status` | Small-signal AC |
| `tran` | `tran1 tran stop=100n` | Transient |
| `noise` | `noise1 noise start=1 stop=1G dec=10 oprobe=RD iprobe=VIN` | Noise |
| `stb` | `stb1 stb start=1 stop=10G dec=10 probe=Iprobe` | Loop stability |
| `pz` | `pz1 pz iprobe=VIN oprobe=RD` | Pole-zero |
| `xf` | `xf1 xf start=1 stop=10G dec=10 probe=RD` | Transfer function |
| `sweep` | `swp1 sweep param=temp values=[-40 27 125] { ... }` | Wrapper |
| `alter` | `alt1 alter param=temp value=85` | Change a param |
| `altergroup` | `ag1 altergroup { parameters W0=4u }` | Grouped alters |

`options` statements look like instances of master `options`:

```
simulatorOptions options reltol=1e-3 vabstol=1e-6 iabstol=1e-12 temp=27 \
    tnom=27 gmin=1e-12
saveOptions options save=allpub
```

Treat them as options, not as circuit instances, when counting devices.

---

## 10. Numbers and unit suffixes

This is the most common silent error between Spectre and SPICE.

### Spectre (SI, case-sensitive)

From Designer's Guide Appendix B, Table B.1:

| Suffix | Scale | Notes |
|---|---:|---|
| `P` | 1e15 | peta |
| `T` | 1e12 | tera |
| `G` | 1e9 | giga |
| `M` | 1e6 | **mega** |
| `K` or `k` | 1e3 | kilo |
| (none) | 1 | |
| `%` | 1e-2 | percent |
| `c` | 1e-2 | centi |
| `m` | 1e-3 | **milli** |
| `u` | 1e-6 | micro |
| `n` | 1e-9 | nano |
| `p` | 1e-12 | pico |
| `f` | 1e-15 | femto |
| `a` | 1e-18 | atto |

So Spectre `1M` = 1e6 and Spectre `1m` = 1e-3.

### SPICE (case-insensitive)

From Designer's Guide Appendix B, Table B.2:

| Suffix | Scale | Notes |
|---|---:|---|
| `t` | 1e12 | |
| `g` | 1e9 | |
| `meg` | 1e6 | **mega** (not `M`) |
| `k` | 1e3 | |
| `m` | 1e-3 | milli. **`M` is also milli** |
| `mil` | 25.4e-6 | |
| `u` | 1e-6 | |
| `n` | 1e-9 | |
| `p` | 1e-12 | |
| `f` | 1e-15 | |

SPICE `1M` = 1e-3. SPICE mega is `1meg`.

**Classic 1e9 bug:** a designer writes `r=1M` meaning 1 megohm. In Spectre that is 1e6 ohm. After `simulator lang=spice` (or in ngspice / HSPICE) it is 1e-3 ohm.

Do not normalize suffixes when parsing. Keep the raw text. Convert only with an explicit Spectre-rule helper.

Also accepted: `1e-6`, `1.8`, `2*W0` (expression, not a literal).

---

## 11. Parameter expressions

`parameters` (Spectre) and `.param` (SPICE) bind names to raw expression text.

Instance values keep the same text. Do not evaluate.

| Form | Example | Where it comes from |
|---|---|---|
| Arithmetic | `w=W0*2` | Netlist / ADE variable |
| Braces | `l={Lmin}` | Spectre grouping / some netlisters |
| Parent design param | `w=pPar("W")` | Virtuoso CDF, parent pPar |
| Design variable | `l=VAR("Lmin")` | ADE design variable |
| Instance param | `nf=iPar("nf")` | Virtuoso CDF iPar |

Fully elaborated ADE netlists often replace `pPar` / `VAR` / `iPar` with numbers. Schematic-level or partially evaluated dumps can leave the function forms in place.

Built-in constants such as `M_PI`, `P_Q`, `P_K` are reserved (see Spectre `keywords` help).

---

## 12. `save` and `options`

```
save vout
save I0.outp
save M0:d
saveOptions options save=allpub
saveOptions options save=selected currents=selected
```

| Token | Meaning |
|---|---|
| `save <node>` | Request that node voltage |
| `I0.outp` | Hierarchical node (dot) |
| `M0:d` | Device terminal (colon): current or contrib |
| `save=allpub` | All public node voltages plus source / inductor / iprobe currents |
| `save=selected` | Only explicit `save` lines. If none are voltages, recent Spectre versions fall back to `allpub` unless `saveselectedtoallpub=nooutput`. |
| `save=lvlpub nestlvl=N` | Like allpub, limited by hierarchy depth |
| `currents=selected` / `all` | Device terminal currents |

ADE "Outputs -> Save all" writes `saveOptions`. ADE "Outputs" pane writes individual `save` lines.

---

## 13. Built-in primitives seen in ADE dumps

| Master | Typical terminals | Notes |
|---|---|---|
| `resistor` | p n | `r=`, optional `tc1` `tc2` |
| `capacitor` | p n | `c=` |
| `inductor` | p n | `l=` |
| `vsource` | p n | `dc=` `mag=` `type=` `freq=` `ampl=` |
| `isource` | p n | same idea |
| `iprobe` | p n | Zero-volt current probe; used by `stb` / `pz` |
| `nch` / `pch` | d g s b | Model names, not primitives |
| `bsim4` / `bsim3v3` | (model master) | Used on `model` lines |
| `options` | (none) | `simulatorOptions`, `saveOptions` |
| `port` | p n | RF / `sp` analyses |

MOSFET instance names in Spectre do **not** have to start with `M`. ADE still usually names them `M0`.

---

## 14. Reserved words (do not use as instance / model / subckt names)

From Spectre `keywords`: `subckt`, `ends`, `end`, `model`, `parameters`, `include`, `library`, `global`, `save`, `altergroup`, `if`, `else`, `for`, `inline`, `statistics`, `to`, `plot`, `print`, `ic`, `nodeset`, and others. `in` and `out` are listed as keywords; ADE still uses them as **node** names constantly, and that works in practice.

---

## 15. What this page does not cover

PSS / PAC / PNOISE / PSP / PSTB, `montecarlo`, `statistics` blocks, `paramset`, `inline subckt` binning, structural `if`, encrypted PDKs, and MDL. Those appear in RF / yield ADE dumps and need the Spectre reference, not this lookup table.
