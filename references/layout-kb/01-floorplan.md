# Floorplan before any shape

tags: floorplan, aspect, area, signal-flow

Decide the floorplan on paper, in one pass, the way a designer does, and then generate it.
Do not discover the floorplan by placing devices and deleting them.

- Signal flow left to right. Power rails on the long edges.
- Every block, not only the top cell, aims at square (1:1). 1.5:1 is a warning. 2:1 fails
  the audit. A long row is a failed floorplan: fold it (two rows of ABBA, not one ABBAABBA).
- Write an area budget first: device area times about 1.5-2.5 for routing. Utilisation under
  ~30 % means the empty space is wire.
- Only a standard-cell-height leaf (one inverter, one flip-flop) may be a thin row, and those
  leaves are tiled into a square logic block.
- Fast blocks (comparator, sampler, clock) sit so their critical nets are short. Slow blocks
  (bias) care about matching and area, not about a femtofarad.

Source: Hastings, *The Art of Analog Layout*, 2nd ed. (2006), floorplanning and matching;
measured failure in this project: phase-1 SAR layouts that were one long row.
