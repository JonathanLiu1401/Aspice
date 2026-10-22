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

- **Aim for square (1:1) - every block, not just the top cell.** A square block has the shortest
  worst-case wire for its area; 2:1 is the hard limit (the audit fails above it, warns above
  1.5). Measured in the phase-2 benchmark: both bridge-lite agents kept the top cell under 2:1
  (1.5-1.6) but drew the CDAC at 2.6-2.8:1 and a sampling switch at 3.7:1. Fold rows (two
  rows of ABBA instead of one ABBAABBA), split a differential array into halves stacked
  around the comparator rather than in one strip, and pick the row count before placing.
  Only standard-cell-height logic leaf cells (one inverter, one flip-flop) are exempt; tile
  them into a square logic block.
- Blocks in signal-flow order, power rails on the long edges.
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

## 4. Routing (a grid, not a drawing)

The flow is laygo2's: templates on a placement grid, wires on a routing grid
(`references/layout-kb/13-laygo-flow.md`, https://laygo2.github.io/overview.html).
`Layout.tracks` and `Layout.connect` are that routing grid. Do not draw polylines.

- One direction per metal. Adjacent layers are orthogonal. Use the PDK's directions if
  its document names them; otherwise Metal1 horizontal, Metal2 vertical, alternating.
  Never both directions on one layer.
- Pitch is what laygrid measured (via landing + minimum spacing, widened by the techfile's
  width table). Every centerline and every pin center is on that pitch. Off-grid metal is
  a defect. Manhattan only: a jog is a via onto the other direction.
- If the techfile defines multi-pattern mask colors (LAYGO's routing-grid coloring), a
  wire stays on one color.
- Minimum width for signals. Supplies, tails, and sampling-switch paths are several tracks
  wide, from the techfile current limit when it has one, and they use via arrays (2x2
  minimum on supplies). One via is only for a tiny signal.
- Clocks and digital cross sensitive analog (comparator inputs, CDAC top plate, bias) at
  right angles and do not run parallel to them. Shields are generated from the same tracks
  as the pair (grounded metal on both sides, or a grounded layer above), not added after a
  simulation. For a sampling switch, cross-coupled dummy metal can cancel input-to-hold
  coupling (LAYGO's TI-SAR).
- Supply: wide VDD/VSS rails on the long edges, analog and digital supplies kept as
  separate nets, well and substrate taps along every row, guard rings around the
  comparator, the CDAC, and the bias.

## 5. Pins, and what is not a pin

- This block is not a chip. No bond pads, probe pads, pad openings, seal ring, or I/O ESD.
  Diffusion contacts and vias are required; they are not pads. Hastings and Baker discuss
  pad frames as chip-level. Do not copy that onto an ADC block.
- Pin shapes on the routing layer, on the grid, at the cell edge, with the schematic's names.

## 6. When post-layout misses

Read verify's worst-net summary, or `scripts/netbrief.py pex` (capacitance in fF, largest
R segment and segment count, not the sum of a supply mesh). Change the floorplan or the
wire budget in `layout_<cell>.py` and regenerate. Do not nudge shapes in the database.
Re-extract that leaf once, on a short testbench, not the whole chip.
