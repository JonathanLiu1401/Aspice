# Devices are PDK instances, never drawings

tags: pcell, instantiation, mosfet, fingers, cdf

A MOSFET, resistor, or capacitor in the layout is an instance of the PDK pcell, with CDF
parameters (finger width, length, fingers, multiplier). It is not a set of diffusion,
poly, and metal rectangles you drew.

- Look up the cell name in the PDK library. Do not hard-code a device from a different kit,
  and do not assume one kit's `w` is total width. Sizing is finger width and finger count.
- Multi-finger devices share source and drain. That sharing is the pcell's job. Do not draw
  it, and do not stretch the pcell to "make it match" — change the parameters or the count.
- `laygrid.Template(lib, cell, params={...})` then `tech.measure`. Pin names come back from
  the pcell (`G`, `S`, `D`, and the PDK's per-finger names). Read `tpl.pins`. Do not guess them.
- Hand-drawn device geometry is a failed layout even if DRC is clean.

Source: Hastings, 2nd ed., MOS transistor layout (multi-finger, unit elements); Clein,
*CMOS IC Layout* (use the device cells the kit provides).
