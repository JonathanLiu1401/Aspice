# Rails, taps, and guard rings

tags: power, rail, tap, substrate, well, guard-ring, latchup

- VDD and VSS are wide rails on the long edges of each row, on a thick metal, on the grid.
  Analog and digital supplies are separate nets even when they share a voltage. Do not strap
  them together inside the block to "make LVS easier."
- Every row has well taps and substrate taps along it, at the spacing the latch-up rule
  requires (the techfile; if you have not read it, the conservative practice is a tap every
  few tens of micrometres, not one tap in the corner). Taps are pcells or the kit's contact
  cells, tied to the right rail.
- A sensitive analog block (comparator, CDAC, bias) gets a guard ring: a continuous
  substrate or well contact around it, tied to a quiet rail. Digital sits outside that ring.
- The ring is part of the generator for that cell, not a rectangle added the hour before tapeout.

Source: Hastings, 2nd ed., wells, substrate contacts, guard rings; Clein, *CMOS IC Layout*.
