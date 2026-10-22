# Schematic Reference

## Edit Pattern

```python
from virtuoso_bridge.virtuoso.schematic import (
    schematic_create_inst_by_master_name as inst,
    schematic_create_pin as pin,
    schematic_create_wire_between_instance_terms as wire,
    schematic_label_instance_term as label,
)

with client.schematic.create(lib, cell) as sch:
    sch.add(inst("analogLib", "vdc", "symbol", "V0", 0, 0, "R0"))
    sch.add(inst("analogLib", "gnd", "symbol", "GND0", 0, -0.5, "R0"))
    sch.add(wire("V0", "MINUS", "GND0", "gnd!"))
    sch.add(label("V0", "PLUS", "VDD"))
    sch.add(pin("VDD", 0, 1.0, "R0", direction="inputOutput"))
    sch.add_net_label_to_transistor("M0", drain_net="OUT", gate_net="IN",
        source_net="VSS", body_net="VSS")
```

`sch.add(skill_cmd)` queues SKILL commands; `schCheck` + `dbSave` run on context exit.

## CDF Parameter Setting

Read the actual device's CDF before setting parameters. Editable names and
callback dependencies differ by PDK; `nf`/`fingers` mappings in an example are
not a universal device contract. For supported PDK devices, use
`set_instance_params`, which handles `schHiReplace` and the CDF callback:

```python
from virtuoso_bridge.virtuoso.schematic.params import set_instance_params

set_instance_params(client, "MP0", w="500n", l="30n", nf="4", m="2")
```

When using raw SKILL, refresh the applicable changed-parameter callbacks in
their documented order, then read back derived values and check/save before
netlisting. A stored property update alone can leave derived parameters stale.

For analogLib devices, direct CDF access works:

```python
client.execute_skill(
    'cdfFindParamByName(cdfGetInstCDF('
    'car(setof(i geGetEditCellView()~>instances i~>name == "R0")))'
    ' "r")~>value = "1k"')
client.execute_skill('dbSave(geGetEditCellView())')
```

## Read Placement

```python
from virtuoso_bridge.virtuoso.schematic.reader import read_placement

p = read_placement(client, "myLib", "myCell")
for i in p["instances"]:
    print(i["name"], i["xy"], i["orient"])
```

## Drawing so the sheet is readable

`schematic_create_wire_between_instance_terms` draws the wire. It does not
autoroute. Cadence's `schCreateWire(..., "route", "full", ...)` ignores every
point after the second and routes around geometry, which produces long
snakes and crossings. The helper uses entry method `"draw"` instead:

- two pins on the same x or the same y become one straight segment;
- otherwise each pin exits a short stub (MOS gate left, NMOS drain up, PMOS
  drain down, body right; other symbols exit the nearest bbox edge) and one
  orthogonal bend joins the stubs without turning back into either symbol.

Place instances so that straight segment is short and obvious. Share an axis
with the pin you are connecting. Keep a different net off that line. Read pin
centers from the master (they are not at the instance origin). Typical analogLib
`vdc` / `res`: PLUS is at the origin and MINUS is 0.375 away — `R0` below,
`R90` to the right, `R180` above, `R270` to the left. Draw supplies with `R0`
or `R180` so the symbol is vertical.

Rasterize after check/save. `capture_schematic_cell(client, lib, cell, path)`
opens the view, fits it, and writes a PNG. Do not treat
`thumbnail_128x128.png` as the picture. If the PNG shows a crossing, a wire
through a symbol, or a detour, move the instance; do not switch the wire back
to `"route"`.

## Tips

- Use `add_net_label_to_transistor` for MOS D/G/S/B — auto-detects stub direction
- Use `schematic_label_instance_term` / `schematic_create_wire_between_instance_terms` instead of guessing coordinates
- **Check & save before simulation**: `schCheck` + `dbSave` — otherwise netlisting fails with a blocking dialog
- **Schematic should be open in GUI** for Maestro to reference it correctly

## See also

- `references/schematic-python-api.md` — Python API reference
