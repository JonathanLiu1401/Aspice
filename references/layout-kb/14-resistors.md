# Resistor layout

tags: resistor, poly, matching, dummy, ratio, kelvin

A matched resistor is copies of one unit, not one drawn rectangle per value.

- Ratio by how many units, or by length of identical width. Do not ratio by width: width errors and end effects do not scale the way a designer assumes.
- Same orientation. A rotated copy sees a different etch. Bends, if you must have them, are the same on every segment.
- Dummies at both ends of the row, tied off, same as a transistor row. The end resistor etches differently.
- Contacts at the ends are part of the unit. A unit with two contacts and a unit with four are not the same resistor. Count them in the generator.
- High-value poly over a well or over field is a different device from poly over diffusion. Do not mix them in one ratio.
- Kelvin sense a precision tap: the current path and the sense path leave at different contacts, so metal resistance is not inside the ratio.
- The resistor is a PDK pcell. Do not draw poly and contacts by hand.

Source: Hastings, *The Art of Analog Layout*, 2nd ed. (2006); Baker, *CMOS Circuit Design, Layout, and Simulation*. The PDK's resistor cell and its sheet-rho notes override a guessed value.
