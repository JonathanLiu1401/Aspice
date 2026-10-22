# Density fill

tags: density, fill, slot, metal-density, cheesing, analog

Foundry density fill is not part of the circuit, and it is not free. Floating or grounded fill next to a sensitive net is capacitance you did not draw.

- Do not draw fill by hand to "make density." The deck inserts fill, or a fill step does, under rules. Your job is to mark the sensitive regions so fill stays out of them when the kit provides an exclude layer. Look that layer up in the techfile. Do not invent the layer name.
- A wide metal that the current-density rule forced on you may need slots so density and stress rules pass. Slot on the grid, and do not slot a shield so that it stops being a shield.
- Fill over the CDAC top-plate route or the comparator input is a bug. If the exclude layer exists, the generator puts it on those nets' bounding regions. If it does not, keep those routes short and shielded so added fill is a small fraction of the node cap, then check the extracted cap.
- Digital blocks can take fill. Analog matching arrays should not take fill between the units. Fill between unit capacitors changes the unit.

Source: Hastings, 2nd ed., and foundry density rules in the techfile. SkyWater SKY130 publishes its periphery rules openly (https://skywater-pdk.readthedocs.io/, Apache-2.0). Those numbers are for that process only. On any other kit, including gpdk045, read the techfile. Do not copy a sky130 number into a gpdk layout.
