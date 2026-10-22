# Sources for the layout technique cards

The files next to this one are original notes of standard analog-layout practice, written so an
agent can search them. They are not the textbooks. Do not treat a card as a quotation, and do not
paste a book into the repository.

Read these when a card is not enough. The placement and matching rules follow Hastings. The metal
grid follows Saint & Saint. The circuit rules that the layout has to serve follow Razavi.

| Source | What to take from it |
|---|---|
| Alan Hastings, *The Art of Analog Layout*, 2nd ed., Pearson Prentice Hall, 2006 | Matching, common centroid, interdigitation, dummies, multi-finger MOS, resistors and capacitors, floorplanning, guard rings, current density, contacts and vias |
| Christopher Saint and Judy Saint, *IC Mask Design: Essential Layout Techniques*, McGraw-Hill, 2002 | Routing pitch, one direction per metal, via landing on the grid, density |
| Dan Clein, *CMOS IC Layout: Concepts, Methodologies, and Tools*, Newnes, 2000 | Mask-layer order, wells, taps, hierarchy of cells |
| R. Jacob Baker, *CMOS: Circuit Design, Layout, and Simulation*, Wiley-IEEE | Layout chapters next to the circuit chapters: matching, centroid, unit elements |
| Behzad Razavi, *Design of Analog CMOS Integrated Circuits* | Operating-point and matching rules the schematic must already satisfy (`references/opcheck.md`) |
| Jaeduk Han, Woorham Bae, Eric Chang, Zhongkai Wang, Borivoje Nikolić, Elad Alon, "LAYGO: A Template-and-Grid-Based Layout Generation Engine for Advanced CMOS Technologies," IEEE TCAS-I, vol. 68, no. 3, March 2021. DOI 10.1109/TCSI.2020.3046524. PDF in `~/EMiT/papers/` | Templates on a placement grid, wires on a routing grid, differential shields from abstract tracks. Their example is a TI-SAR ADC |
| laygo2, Niftylab, Hanyang University, https://laygo2.github.io/ (BSD-3) | The flow to follow: load templates and grids, generate, place, route, pin, export, reuse the cell as a template. Overview: https://laygo2.github.io/overview.html |
| Felicia Guo et al., "BAG3++: An Extensible Generator Framework for Automated Layout-Aware AMS Design," IEEE Open Journal of Circuits and Systems, 2025. DOI 10.1109/OJCAS.2024.3502641. PDF in `~/EMiT/papers/` | One generator owns schematic, layout, and extracted checks. Their SAR ADC extracts the layout instead of treating layout as a later hand step. Digital place-and-route is the wrong tool for the fast analog cells |

A card that conflicts with the PDK techfile loses. The techfile's spacing, width, via, and current
rules are measured by laygrid; the books say how to use them.
