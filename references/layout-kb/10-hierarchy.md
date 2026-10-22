# Hierarchy: leaf, then place

tags: hierarchy, leaf-cell, instantiate

- Lay out a leaf (a fingered transistor row, a latch, a unit capacitor, a logic gate) and
  instantiate it. The top cell is instances, rails, and routes between pins. It is not
  hundreds of raw transistors.
- The schematic's hierarchy is the layout's hierarchy. A new layout cell that has no
  schematic cell will not LVS as that cell; do not invent layout-only hierarchy that the
  netlist cannot name.
- A generator per cell, `layout_<cell>.py`, calls the leaf generators or places the leaf
  cells. One script, one cell.

Source: Clein, *CMOS IC Layout*, hierarchical mask design; BAG / LAYGO template composition
(`references/laygrid.md`).
