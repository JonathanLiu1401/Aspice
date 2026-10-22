# aspice rules

EMiT Lab research codebase: an agent designs analog CMOS in Cadence Virtuoso, from a
readable schematic through a layout a working designer would ship, including parasitic
extraction. These rules override speed, examples, and anything you remember from a
previous trial. If a step below says stop, stop.

## 0. What you are not doing

You are not searching for a layout. A loop of "draw something, simulate, nudge" is a
failed run even if the last iteration meets spec. You are not drawing transistors. You
are not building a chip pad frame. You are not reading raw netlists.

## 1. The only layout procedure

Follow laygo2's flow (https://laygo2.github.io/overview.html), implemented in this repo
by `lib/laygrid.py`. Details and the paper mapping: `references/layout-kb/13-laygo-flow.md`.
Search before you invent a structure:

```bash
python <aspice>/scripts/layout_search.py "capacitor array common centroid"
```

1. Schematic is finished and `scripts/opcheck.py` passes. Do not start layout before that.
2. Write the floorplan in the generator's docstring: blocks, one symmetry axis, which
   metal is which direction, area budget. Search the technique cards first
   (`references/layout-kb/`). The placement rules are Hastings, *The Art of Analog
   Layout*, 2nd ed. (2006). The grid is Saint & Saint, *IC Mask Design* (2002), and
   LAYGO (Han et al., IEEE TCAS-I, March 2021).
3. Devices are PDK pcell instances (`laygrid.Template`). Never rectangles of diffusion
   and poly.
4. One Python file per cell, `layout_<cell>.py`. It measures templates, places rows
   (`common_centroid`, `interdigitate`, `with_dummies`), routes with `tracks` and
   `connect`, and writes the cell once.
5. One DRC. Failures are fixed in the script, then the script is re-run. Do not patch
   the database.
6. LVS, then one post-layout check of each fast leaf (comparator, sampler, clock).
   Read verify's worst-net summary or `scripts/netbrief.py pex`. Change the floorplan
   or the wire budget in the script and regenerate.
7. Assemble by instantiating those cells. One short top-level check (ADC: at most 16
   conversions, typical corner). One long check, both corners, at the end.
8. Hand edits only after step 5 is clean, and only for a pin, a shield, or a short jog
   the router missed. Apply a differential edit to both halves. If you are placing
   devices by hand, delete them and go back to step 4.

Stop conditions:

- You are about to draw a MOSFET, resistor, or capacitor as shapes. Stop. Use a pcell.
- You are about to add a bond pad, probe pad, pad opening, seal ring, or I/O ESD. Stop.
  This ADC is a block, not a chip. Diffusion contacts and vias stay; those are not pads.
- The top cell or any block is past 2:1, or you are about to leave it as one long row.
  Stop. Fold it. Target is 1:1.
- You have run a top-level simulation to decide a single wire. Stop. That decision
  belongs in the generator.
- You are about to open an extracted netlist, a CDL, a PSF, or a DRC dump. Stop. Use
  the brief.

## 2. Metal grid

| Rule | Requirement |
|---|---|
| Direction | One direction per layer. Adjacent layers are orthogonal. Use the PDK's directions if documented; otherwise Metal1 horizontal, Metal2 vertical, alternating |
| Pitch | laygrid's measured pitch (via landing + minimum spacing). Centerlines and pin centers on the pitch. No off-grid metal |
| Corners | Manhattan only. A jog is a via to the other direction, on grid |
| Width | Minimum for signals. Supplies, tails, and switch paths are multiple tracks, set from the techfile current limit when it has one |
| Vias | An array, not one cut, on supplies, tails, and any net carrying more than one device. 2x2 minimum on supplies |
| Colors | If the techfile has multi-pattern mask colors, a wire stays on one color. Do not mix colors to squeeze a route |
| Pins | On the routing layer, on the grid, on the cell edge, schematic names |
| Shields | Sensitive nets (comparator input, CDAC top plate, bias, hold) get grounded side shields or a grounded metal above, generated from the same tracks as the pair. Clocks cross at 90 degrees and do not run parallel |

## 3. Matching and hierarchy

- Matched devices: same pcell, same W, L, fingers, orientation. Ratios are counts of unit fingers.
- Pairs that must reject a gradient: common centroid (ABBA or 2-D cross-coupled). Mirrors: interdigitated units. Dummies at both ends of every matched row and around capacitor arrays, tied off.
- One symmetry axis for the whole differential block. Both halves come from the same generator.
- Capacitor DAC: identical unit capacitors, not one drawn plate per bit. Two halves on either side of the comparator, each close to square. Not one wide strip.
- Leaf cell first, then instantiate. The top layout is instances, rails, and routes between pins.
- Rails on the long edges. Substrate and well taps along every row. A guard ring around the comparator, the CDAC, and the bias. Analog and digital supplies stay separate nets.

`scripts/layout_audit.py LIB CELL --area-budget A --pairs-from tb.scs` checks this.
A FAIL is a generator bug.

## 4. When numbers are allowed

Order is fixed: schematic, opcheck, block tests, generator, one DRC, fast-leaf extract,
assemble, one short top check, one long check. More simulations than that means the
generator is being used as a search. Waveforms, raw netlists, and PSF stay out of the
conversation.

```bash
python <aspice>/scripts/netbrief.py scs path/to/netlist.scs
python <aspice>/scripts/netbrief.py pex path/to/extracted.sp
python <aspice>/scripts/layout_audit.py LIB CELL
```

`layout_audit` prints a master rollup, not every instance. `CD.layout` is the same kind
of summary. Do not dump shapes.

## 5. Ship bar

DRC clean and LVS match are necessary and not sufficient. The extracted netlist is within
10 % of the schematic on the key measures at the typical RC corner (an ADC: ENOB within
0.25 bit) and still meets spec at the worst corner (`scripts/verify.py`). A designer who
has laid out analog for decades would accept the block: on-grid, matched, tapped, no pad
frame, hierarchy intact, regenerable from the script.

## 6. Where to look

| Question | File |
|---|---|
| Project rules | this file |
| Skill entry, schematic and sign-off rules | `SKILL.md` |
| Searchable layout practice | `references/layout-kb/` and `scripts/layout_search.py` |
| Geometric checklist | `references/layout-guidelines.md` |
| laygrid API | `references/laygrid.md` |
| Token and simulation budget | `references/efficiency.md` |
| Sign-off command | `references/verify.md` |
| Operating point | `references/opcheck.md` |

Technique cards cite the books and the two papers in `~/EMiT/papers/`. They are notes,
not the books. The techfile wins over a card when a number disagrees.
