# Bias and reference distribution

tags: bias, IR-drop, kelvin, star, current-mirror, routing

A current mirror that matches in the schematic and not in silicon is usually the metal, not the transistors.

- The gate line of a mirror is metal, wide enough that its drop is negligible compared with the overdrive, and it is routed symmetrically to every gate. A long skinny poly gate link between fingers of different branches is a mismatch.
- Star-connect matched branches when the current is large: each branch gets its own metal from a common point, same length. Do not daisy-chain branches off one run so the last device sees the IR drop of all the others.
- The tail or reference device sits on the symmetry axis, or is itself common-centroid, and its output metal leaves from the center.
- Separate the force path from a sense path on a precision reference (Kelvin). The bond-pad case does not apply here; the same idea does apply at the block pin.
- Analog bias does not share metal with a digital supply, even for a short jog.

Source: Hastings, 2nd ed., interconnect resistance and matching; Razavi, 2nd ed. (2017), reference distribution. Check the worst-net resistor list from `scripts/netbrief.py pex` after the generator, not after each wire.
