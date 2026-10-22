# Shielding and what may run next to what

tags: shield, coupling, clock, analog, crosstalk

Coupling is planned, not discovered in a post-layout sim and patched with a random shield.

- Sensitive nets: comparator inputs, CDAC top plate, bias, sample-and-hold. Clocks and
  digital run on an upper metal, cross those nets at right angles, and do not run parallel
  to them. A long parallel run is a redraw of the floorplan.
- Shield a sensitive run with grounded metal on both sides on the same layer, or with a
  grounded plane on the layer above, tied down with a via array often enough that the shield
  is not itself a floating antenna. Ground means the analog quiet rail, not a clock buffer's
  local supply.
- Differential pairs are routed as a matched pair: same layer, same length, same via count,
  same neighbour on the outside. The match of the parasitics matters as much as their size.
- For a sampling switch, a cross-coupled dummy metal can cancel input-to-hold coupling.
  That dummy is drawn in the generator, symmetric, not added after a sim looks bad.

Source: Hastings, 2nd ed., coupling and shielding; LAYGO TI-SAR practice already noted in
`references/layout-guidelines.md`.
