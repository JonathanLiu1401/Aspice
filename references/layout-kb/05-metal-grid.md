# Metal grid: one direction per layer

tags: metal, grid, pitch, preferred-direction, manhattan, routing

Interconnect is a grid. It is not a polyline you adjust until DRC passes.

- Adjacent metals run in opposite directions. If the PDK document names the directions, use
  those. If it does not, Metal1 horizontal, Metal2 vertical, and alternate. Never both
  directions on one layer "just this once."
- Pitch is whatever laygrid measured: via landing plus the minimum spacing, widened if the
  techfile's width table says so. Every wire centerline and every pin center is on that pitch.
  A via can land on every track crossing. Off-grid metal is a defect, not a shortcut.
- Manhattan only. No diagonals, no arbitrary jogs. A jog is a track change through a via,
  on grid.
- Minimum width for signals. The grid pitch already fits that width. Do not neck a wire
  below the rule to squeeze a route; move a row instead.
- Pins are rectangles on the cell's routing layer, on the grid, on the cell edge, with the
  schematic's net names. Not a text label floating in the middle.

Source: Saint & Saint, *IC Mask Design* (2002); Hastings, 2nd ed., interconnect.
Tool: `Layout.tracks` and `Layout.connect` in `lib/laygrid.py`. The pitch is measured from
the techfile. No PDK number belongs in your script.
