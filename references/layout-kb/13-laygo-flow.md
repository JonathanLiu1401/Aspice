# The only layout flow: LAYGO2, implemented here by laygrid

tags: laygo, laygo2, bag, generator, template, grid, route, place

Do not place shapes by hand and do not install a second layout tool. The flow is the
one in laygo2 (https://laygo2.github.io/overview.html). In this repository it is
`lib/laygrid.py`. Map their steps onto these calls:

| laygo2 | here |
|---|---|
| `load_templates()`, `load_grids()` | `Tech.load`, then `Template` of a PDK pcell, then `tech.measure` |
| `Template.generate()` | one measured template per device size |
| `Design.place()` | `Layout.row` with `common_centroid`, `interdigitate`, `cross_coupled`, `with_dummies` |
| `Design.route()`, `route_via_track()` | `Layout.tracks` then `Layout.connect` |
| `Design.pin()` | pin shapes on the routing layer, on the grid, at the cell edge, schematic names |
| `export` (SKILL / skillbridge / GDS) | `Layout.write` into the open Virtuoso, then `scripts/verify.py` |
| save as a template and reuse | the leaf cell is instantiated in the parent generator. Do not flatten it |

What the papers require, in this order:

1. **Templates on a placement grid** (Han et al., IEEE TCAS-I, March 2021, DOI
   10.1109/TCSI.2020.3046524). A template encapsulates the device and the design rules.
   You place templates by relative position on the grid, not by absolute coordinates you
   guessed. The grid is process-specific and is measured from the techfile.
2. **Wires on a routing grid.** Same paper: interconnect is on-grid so the design rules
   stay inside the grid, including multi-pattern coloring when the techfile has mask
   colors. A differential pair and its shields are generated from the same abstract
   tracks (same length, same via count, shields beside the pair). They are not drawn
   after a simulation looks bad.
3. **One Python generator per cell**, then export. laygo2's design flow is load, generate,
   place, route, pin, export, and optionally reuse the cell as a template. Final touches
   are a few shapes after that export is DRC-clean. If you are looping in the GUI, you
   have left the flow.
4. **Layout-aware, not schematic-then-hope** (Guo et al., BAG3++, IEEE OJCAS, 2025, DOI
   10.1109/OJCAS.2024.3502641). Their SAR ADC is a generator: capacitor DAC, sampler,
   logic, then extracted simulation of that layout. Digital place-and-route is the wrong
   tool for the fast analog cells (it forces standard-cell shapes and ignores the
   parasitics that dominate). Extract the fast leaf before assembling the ADC.

The TI-SAR in the LAYGO paper is a full chip with its own pads. This project's ADC is a
block. Follow their generator and grid. Do not follow their pad frame. See `11-not-a-chip.md`.
