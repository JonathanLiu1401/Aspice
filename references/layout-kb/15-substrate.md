# Substrate coupling

tags: substrate, guard-ring, deep-nwell, noise, digital, analog

Digital switching current in the substrate shows up on a high-impedance analog node. Distance alone is a weak fix. The generator places the isolation.

- Sensitive analog (comparator, CDAC top plate, bias, reference) sits in its own well region, surrounded by a continuous guard ring tied to a quiet rail. A ring with a gap is not a ring.
- Analog and digital do not share a substrate tap or a ground metal. Tie them at the block pins, not inside the cell.
- A deep n-well, if this PDK has the layer, isolates a p-well island for the sensitive devices. Look the layer up. Do not invent one the kit does not have.
- Clocks and large inverters go outside the ring, on the far side of the logic block, not beside the comparator inputs.
- Substrate taps along every row are for latch-up. The guard ring is for coupling. You need both.
- Do not "fix" coupling by adding a random grounded metal after a simulation. Move the noisy block or close the ring in the generator, then extract once.

Source: Razavi, *Design of Analog CMOS Integrated Circuits*, 2nd ed. (McGraw-Hill, 2017), layout and packaging discussion; Hastings, 2nd ed., guard rings and wells.
