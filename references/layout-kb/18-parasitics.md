# Interconnect R and C, planned

tags: parasitic, capacitance, resistance, metal-stack, shielding, wire

Budget the wire before routing it. Extraction confirms the budget. It does not choose the floorplan.

- Minimum-width metal is about 0.1-0.2 fF per micrometre to the substrate and to neighbors, in a typical bulk CMOS stack. Use that only as a first cut. The techfile capacitance tables, if present, replace it. If a node's wire cap reaches ~10 % of its device cap, the route is too long: move the devices.
- Lower metals have higher capacitance to the substrate and to poly. A sensitive analog run (CDAC top plate, comparator input, hold) prefers a higher metal, short, with side shields, not a long run on Metal1.
- Poly and diffusion are resistors. Do not route a signal in them. Gates are contacted to metal near the device and the metal does the traveling.
- Series resistance on a switch, a DAC bottom plate, or a clock is settling error. Widen on-grid or add a via array. Do not leave the grid to "make a pour."
- Coupling C between two nets is the term that moves a comparator threshold and a sample. The worst-net list names it. Change the generator (separation, shield, or a different layer), regenerate, extract once.

Source: Razavi, 2nd ed. (2017), interconnect capacitance and resistance; Saint & Saint, *IC Mask Design* (2002), layer choice. LAYGO routes these on the grid so the rule deck, not a hand jog, sets the spacing (`13-laygo-flow.md`).
