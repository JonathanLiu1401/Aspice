# Layout guidelines (analog, any PDK)

Used with the hard rule in SKILL.md: the layout is done when its extracted netlist performs like the schematic
(`virtuoso-bridge verify LIB CELL --steps drc,lvs,pex,post`).

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

When post-layout misses the target, read the extracted netlist: sum the coupling and
ground caps per net (largest first) and the largest resistors; the worst nets tell you
what to move or widen.
