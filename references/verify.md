# verify.py: layout sign-off and post-layout closure


**A layout is done when it is DRC clean, LVS MATCH, and its extracted (PEX) netlist meets
the same specs as the schematic, at the typical AND the worst RC corner.** DRC/LVS clean
alone is not done: a clean layout with long wires on fast or high-impedance nodes can be
several times slower than its schematic (a StrongARM comparator laid out as one 66 um row:
189 ps schematic, 1.11 ns typical PEX, 1.22 ns rcworst).

Hard rules:
1. Every block with a performance spec gets a small SPICE testbench with `.measure`
   lines for that spec, and `python <aspice>/scripts/verify.py LIB CELL --steps drc,lvs,pex,post`
   runs on it before the block is instantiated anywhere else.
2. Post-layout target: each key measure within **10 %** of the schematic value at the
   typical corner, and the spec still met at the worst corner. If not, change the
   generator (`layout_<cell>.py`: placement, wire length, width, shielding, symmetry)
   and regenerate. Do not nudge shapes and re-save. A layout found by repeated
   hand edits is a failed method even if the last run meets the numbers.
3. Do not hide a post-layout gap by re-sizing devices only in the schematic. If you do
   resize, re-run the schematic checks and regenerate the layout; the two must agree.
4. Report the pre/post table from `verify`, not estimates, and what the generator change was.
5. The procedure is `AGENTS.md`. The geometric checklist is `references/layout-guidelines.md`.

## Command

Save the cellviews in Virtuoso first (batch tools read the database on disk).

```bash
python <aspice>/scripts/verify.py LIB CELL --steps drc,lvs,pex,post \
    --corners typical,rcworst --testbench tb_CELL.sp [--cds-lib ./cds.lib] [--env ./.env] [--json]
```

Steps: `drc`, `lvs`, `pex`, `post` (any subset; `pex`/`post` imply LVS). Each prints
PASS/FAIL with the reason; work files are under `VB_PV_WORK_DIR/LIB__CELL/` (LVS
report: `lvs/CELL.lvsrpt.cls`, DRC results: `drc/CELL.drc.ascii`, extracted netlist:
`pex_<corner>/CELL.pex.<corner>.sp`).

Testbench (SPICE syntax; `{{DUT}}` becomes the schematic CDL or the PEX netlist,
`{{MODELS}}` the configured model files). Use the schematic's pin order on the X line:

```spice
* comparator clock-to-output delay
{{MODELS}}
{{DUT}}
VDD vdd 0 1.0
VCLK clk 0 PULSE(0 1.0 1n 20p 20p 4.98n 10n)
VP inp 0 0.5005
VN inn 0 0.4995
XDUT clk inn inp outn outp vdd 0 comp
CL1 outp 0 2f
CL2 outn 0 2f
.tran 1p 12n
.measure tran tdelay TRIG v(clk) VAL=0.5 RISE=2 TARG v(outp) VAL=0.5 RISE=1 TD=11n
.end
```

Tips: give the circuit enough time to settle in the testbench (a `failed` measure in
PEX but not in the schematic usually means the post-layout circuit is too slow for the
window: that is a finding, not a testbench bug; widen the window once to measure how
slow). Python API: `sys.path.insert(0, "<aspice>/lib"); from aspverify import run_signoff`.

## Configuration

Settings live in the profile `.env` (no PDK is assumed by the code):
`VB_PV_DRC_RULES`, `VB_PV_LVS_RULES`, `VB_PV_RUN_FILES` (files the decks include, e.g.
`pvs_control_file`), `VB_QRC_CORNERS` (`typical=<techdir>,rcworst=<techdir>`),
`VB_PV_MODELS` (`<file>@<section>;...`), `VB_PV_CDS_LIB`, `VB_PV_WORK_DIR`, and tool
paths `VB_PVS_BIN`, `VB_QRC_BIN`, `VB_SI_BIN`, `VB_STRMOUT_BIN`, `VB_SPECTRE_BIN`.
Licenses come from the Cadence environment (`VB_CADENCE_CSHRC`). The LVS deck must
support Quantus data (PVS `-rc_data`).

## Layout guidelines (analog, any PDK)

Floorplan
- Sketch the floorplan before drawing: blocks in signal-flow order, die aspect ratio
  at most 2:1, power rails on the long edges. Never lay out a block as one long row.
- Lay out leaf cells first and instantiate them; keep the hierarchy of the schematic.

Placement
- Put devices that share a fast or high-impedance node next to each other, so that node
  is short. Budget roughly 0.1-0.2 fF per um of minimum-width wire: if the wire cap of a
  node exceeds the device cap on it, re-place before routing.
- Matched devices (input pairs, mirrors, latch pairs, unit capacitors): same orientation,
  same surroundings, interdigitated or common-centroid, dummies at array ends.
- Differential circuits: mirror-symmetric about one axis; route both halves identically
  (same length, layers and vias), so parasitics are matched, not just small.

Routing
- One preferred direction per metal layer. Short, direct routes for critical nets; use
  wider metal or parallel vias where current or resistance matters (supply, tail, switch
  paths); minimum width only for low-current signals.
- Keep clocks and digital lines away from (or shielded from) sensitive analog nodes
  (comparator inputs, CDAC top plate, bias lines); never run them in parallel over long
  distances. Cross at right angles.
- Supply: wide VDD/VSS rails, well and substrate taps along every row (latch-up rules),
  guard rings around sensitive analog blocks.

Pins
- Pin shapes on the routing layer at block edges, with names exactly as in the schematic.

When post-layout misses the target, run `scripts/netbrief.py pex` on the extracted file.
It reports capacitance in fF and the largest resistor segment, not a sum across the
supply mesh. Change the generator from that list and regenerate. Do not open the
extracted deck.
