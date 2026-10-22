# Capacitor arrays (the CDAC)

tags: capacitor, CDAC, unit-cap, mim, common-centroid, dummy

A DAC or a matched capacitor is an array of identical unit capacitors, not one drawn plate
per code and not a single large device per bit.

- One unit pcell. Bit k is 2^k copies of that unit, in a common-centroid or interdigitated
  map, with dummies around the array. The two differential halves mirror each other.
- The top plate (the sensitive node) is shielded: grounded metal beside or over the routing
  that leaves the array, and no clock routed parallel to it. A wire on that node is budgeted
  at about 0.1-0.2 fF per micrometre of minimum-width metal; if the wire approaches the unit
  capacitance, move the array, do not add a fudge capacitor in the schematic.
- Route the bottom plates on the grid, same length per unit as far as the pattern allows.
  Series resistance of the bottom plate is part of settling. Wider metal or more vias, still
  on pitch, not a hand-drawn pour that leaves the grid.
- The array is its own cell, nearly square, instantiated in the ADC. It is not a strip down
  the side of the chip. Phase-2 runs that left the CDAC at ~2.7:1 failed this.

Source: Hastings, 2nd ed., capacitor layout and matching; LAYGO's template-and-grid arrays
(`references/laygrid.md`).
