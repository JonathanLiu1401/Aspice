# Drawing a schematic a human can review

Read this before creating any schematic. You draw each block yourself: choose where every device goes and which
terminals each wire joins. Do not write a generic "device list -> schematic" generator that labels every terminal;
that produces a correct netlist nobody can read, and it is the failure this guide exists to prevent.

## What "readable" means

- Nets are **drawn wires between terminals**. A short stub with a net label on its end is not a visible
  connection; inside a block it is a defect.
- **Labels only where a wire would hurt:** a net used in many places (supplies, clock, bias, a bus fanning out),
  a connection that would otherwise cross other wires or run across the sheet, and pins (nets leaving the block).
- Connected devices sit **close together**; wires are short straight runs or one bend.
- **As few crossings as possible.** If two nets must cross, replace one of them with a label pair.
- Signal flows left to right (inputs left, outputs right), VDD at the top, VSS at the bottom.

## Method, per block

1. **Plan on paper first.** Identify the branches from VDD to VSS: devices in series (one's drain or source is
   the next one's source or drain) form a vertical column, top to bottom. A differential circuit has two mirrored
   halves: draw them as mirror images about a centre line, gates facing the centre or the inputs. Devices that
   share a gate net (current mirrors, cross-coupled pairs) go side by side. Put the tail / bias device under the
   pair it feeds.
2. **Read the symbols, do not assume them.** Pin positions differ between PDKs and libraries. For each master you
   use, read its terminals and their pin locations from the symbol (`dbOpenCellViewByType(lib cell "symbol")`,
   `master~>terminals~>pins~>fig~>bBox`) and its bounding box. On many MOS symbols drain, body and source lie on
   one vertical line with the gate to one side: a wire leaving the drain must leave away from the body terminal,
   never along that line through the device.
3. **Place each device at chosen coordinates** (`dbCreateInst` / the bridge's instance helpers) on the schematic
   grid, with room between columns for gate wires (about one symbol width). Shift a device so the terminal it
   shares with the device above lines up vertically: then the stack wire is one straight segment. Mirror a device
   (`MY`) when its gate should face the other way.
4. **Wire terminal to terminal.** Use `schematic_create_wire_between_instance_terms` (entry method `"draw"`), or
   `schCreateWire(cv "draw" "full" points ...)` with points you computed from the terminal positions. Never the
   autorouter (`"route"`).
5. **Avoid accidental connections.** A wire whose end or corner touches another net's wire, or that runs over
   another net's terminal, connects them (a short). Overlapping collinear wires of two nets merge them. Plain
   crossings without a dot do not connect, but count them and keep them rare. When a clean path does not exist,
   use a label pair for that one connection.
6. **Hierarchy keeps sheets small.** Repeated structures (gates, DAC bits, flip-flops) become their own cells with
   symbols; each sheet should be small enough to wire by hand.

## Worked pattern (it works: small test benches drawn this way read like a textbook figure)

```python
# 1. read each master's pin centres once (at R0) and keep them as constants
#    e.g. by SKILL: foreach(t master~>terminals printf("%s %L\n" t~>name centerBox(car(t~>pins)~>fig~>bBox)))
MOS = {"D": (dx_d, dy_d), "G": (dx_g, dy_g), "S": (dx_s, dy_s), "B": (dx_b, dy_b)}   # read, never guessed
VDC = {"PLUS": (..), "MINUS": (..)}
# 2. place every part relative to the terminal it connects to, so the wire is straight:
#    device at (x0, y0); its drain at (x0+dx_d, y0+dy_d); the supply source directly above that drain,
#    upright (PLUS on top); the input pin level with the gate; ground symbol directly below the source
# 3. one explicit wire per connection, named terminal to terminal
sch.add(wire("VDS", "PLUS", "M0", "D"))       # schematic_create_wire_between_instance_terms, "draw"
sch.add(wire("VDS", "MINUS", "G1", "gnd!"))
sch.add(wire("PIN0", "ipin", "M0", "G"))
# 4. check, render, look: sources lying on their side, wires doubling back, labels where a wire fits -> fix, rebuild
```

For a larger block, do the same column by column: place the device at the top of a branch, then each device below
it so the shared terminal lines up vertically, then the next branch beside it.

## Done means checked

A block is finished only when all three hold:

1. `schCheck` is clean and the readback connectivity matches what you intended.
2. The lint passes (it counts, it draws nothing):
   ```python
   import sys; sys.path.insert(0, "<aspice>/lib"); import schlint
   r = client.execute_skill(schlint.lint_skill(LIB, CELL, allow_nets=("VDD", "VSS", "CLK")))
   # ASPLINT <lib> <cell> wires=.. labels=.. share=.. stubs=.. crossings=.. joined=.. PASS|FAIL
   ```
   `allow_nets` lists the nets you labelled on purpose (supplies, clock, buses). FAIL means too many labels or
   labelled stubs: redo the placement and wiring, do not just relabel.
3. You rendered the schematic to a PNG (`capture_schematic_cell`, or an export of the open window) and looked at
   it: every net can be followed by eye, nothing overlaps, crossings are rare.
