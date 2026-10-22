# Layout guidelines (analog, any PDK)

Used with the hard rule in SKILL.md: the layout is done when its extracted netlist performs
like the schematic (`scripts/verify.py LIB CELL --steps drc,lvs,pex,post`). Check matching
and floorplan with `scripts/layout_audit.py`; generate rows and arrays with `lib/laygrid.py`
(`references/laygrid.md`).

## 0. How much layout effort does this block need? (speed decides)

Parasitics barely matter for a slow block and dominate a fast one. Before drawing, estimate
for each node: required time constant (settling, delay, bandwidth) and the device
capacitance on it. The budget for wiring capacitance is roughly 10 % of the node's own
capacitance for a 10 % speed loss.

| Class | Examples | Layout priority |
|---|---|---|
| Slow / DC | bias generators, references, slow OTAs (kHz-MHz) | matching, area, dummies; wire length secondary |
| Medium | SAR logic, DAC switches, 10-100 MHz amplifiers | short critical nets, compact rows |
| Fast | comparators, sampling switches, clock paths, GHz amplifiers, drivers | every femtofarad on latch / output / clock nodes; draw and extract the critical cell FIRST (BAG3 "partial layout"), before the rest of the chip |

Measured: a StrongARM laid out as one 66 um row was LVS clean and 5.9x slower after
extraction (189 ps schematic, 1.11 ns PEX; ~6.8 fF wire on the latch nodes). In phase-1
benchmarks none of the 14 SAR ADC layouts met spec after extraction, while the slow 5T OTA layouts kept post-layout scores of 23-79 of 100.

## 1. Area budget first

- Before drawing, write down an area budget per block: sum of device areas x 1.5-2.5
  (routing overhead) for analog, standard-cell density for logic. Pass it to
  `layout_audit.py --area-budget` / `Layout.report(area_budget=...)`; over budget fails.
- Device utilisation (device area / cell area) below ~30 % means devices are far apart:
  that empty space is wire length on some node.

## 2. Floorplan

- Blocks in signal-flow order, aspect ratio at most 2:1, power rails on the long edges.
  Never lay out a block as one long row: fold rows (two rows of ABBA instead of one
  ABBAABBA; cross-coupled 2-D arrays for pairs).
- Lay out leaf cells first and instantiate them; keep the schematic's hierarchy.

## 3. Placement and matching

- Devices that share a fast or high-impedance node sit next to each other. Budget about
  0.1-0.2 fF per um of minimum-width wire: if a node's wire cap exceeds its device cap,
  re-place before routing.
- Matched devices (input pairs, mirrors, latch pairs, unit capacitors, DAC units):
  - identical devices: same master, W, L, fingers, orientation;
  - common centroid (ABBA, or 2-D cross-coupled ABAB/BABA) for pairs that must cancel
    gradients; interdigitated (ABAB) for ratioed mirrors built from unit fingers;
  - **dummies at both ends of every matched row** (and around unit-capacitor arrays), so
    every unit sees the same neighbours; tie dummies off (gate to source/rail);
  - ratio mirrors by multiplying identical unit fingers, never by a different finger width.
- Differential circuits: mirror-symmetric about ONE axis shared by all pairs; route both
  halves identically (same length, layers, vias), so parasitics are matched, not just small.
- `layout_audit.py LIB CELL --pairs-from tb.scs` takes the pairs and mirrors opcheck
  found in the schematic and checks they are identical, on one row, about one axis, with
  dummies at the row ends; `--groups A=^MA,B=^MB` checks common centroid.

## 4. Routing

- One preferred direction per metal layer; route on tracks (pitch = via landing + min
  spacing). Short, direct routes for critical nets; wider metal or parallel vias where
  current or resistance matters (supply, tail, switch paths).
- Clocks and digital lines away from (or shielded from) sensitive analog nodes
  (comparator inputs, CDAC top plate, bias lines); never parallel over long distances;
  cross at right angles. For sampling switches, cross-coupled dummy metal can cancel the
  input-to-hold coupling (LAYGO TI-SAR).
- Supply: wide VDD/VSS rails, well and substrate taps along every row (latch-up rules),
  guard rings around sensitive analog blocks.

## 5. Pins

- Pin shapes on the routing layer at block edges, with names exactly as in the schematic.

## 6. When post-layout misses

Read verify's worst-net list (coupling + ground cap per net, largest resistors): move or
widen what it names, re-save, re-run. Iterate on the critical cell alone (short testbench),
not on the whole chip.
