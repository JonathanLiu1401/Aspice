# Layout Reference

## Create and Modify Pattern

```python
from virtuoso_bridge.virtuoso.layout import (
    layout_create_rect as rect,
    layout_create_path as path,
    layout_create_label as label,
    layout_create_polygon as polygon,
    layout_create_param_inst as inst,
    layout_create_via_by_name as via,
    layout_create_simple_mosaic as mosaic,
)

with client.layout.modify(lib, cell) as lay:
    lay.add(rect("M1", "drawing", 0, 0, 1, 0.5))
    lay.add(path("M2", "drawing", [(0, 0), (1, 0)], 0.1))
    lay.add(label("M1", "pin", 0.5, 0.25, "VDD", "centerCenter", "R0", "roman", 0.1))
    lay.add(polygon("M3", "drawing", [(0, 0), (1, 0), (1, 1), (0.5, 1.5)]))
    lay.add(inst("tsmcN28", "nch_ulvt_mac", "layout", "M0", 0, 0, "R0"))
    lay.add(via("M1_M2", 0.5, 0.25))
    lay.add(mosaic("tsmcN28", "nch_ulvt_mac", rows=2, cols=4,
                   row_pitch=0.5, col_pitch=1.0))
```

- `client.layout.create(...)`: create new (overwrites)
- `client.layout.modify(...)`: append to existing

## Read / Query

```python
from virtuoso_bridge.virtuoso.layout import layout_read_geometry, layout_list_shapes, layout_read_summary

r = client.execute_skill(layout_read_geometry(lib, cell))
r = client.execute_skill(layout_list_shapes())
r = client.execute_skill(layout_read_summary(lib, cell))
```

## Control

```python
from virtuoso_bridge.virtuoso.layout import (
    layout_fit_view, layout_show_only_layers, layout_highlight_net,
    clear_current_layout, layout_clear_routing,
    layout_delete_shapes_on_layer, layout_delete_cell,
)

client.execute_skill(layout_fit_view())
client.execute_skill(layout_show_only_layers([("M1", "drawing"), ("M2", "drawing")]))
client.execute_skill(layout_highlight_net("VDD"))
client.execute_skill(clear_current_layout())
client.execute_skill(layout_delete_shapes_on_layer("M3", "drawing"))
client.execute_skill(layout_delete_cell(lib, cell))
```

## Tips

- **Read before routing**: use `layout_read_geometry()` to get real coordinates, don't guess from labels
- **Large edits**: start with `create()`, then use `modify()` for subsequent batches
- **Via names**: query `techGetTechFile(cv)~>viaDefs` via `execute_skill()` if unsure
- **Mosaic pitch**: origin-to-origin spacing, not edge gap. Derive from measured bbox
- **Labels on metal**: anchor directly on the metal shape, not beside it
- **Screenshot after edits**: visually verify geometry, don't trust coordinates alone

## Display / Level-of-Detail (LoD) gotcha

Cadence's layout viewer culls shapes that would render below a screen-pixel threshold (~3 px). At fit-zoom of a large cell, **small rects render as nothing** — even though they're physically present.

**Diagnose:** the data is on disk but the canvas is empty.
```scheme
; this should report a non-empty bbox and shape count
let((cv) cv = dbOpenCellViewByType(LIB CELL "layout" "maskLayout" "r")
  sprintf(nil "shapes=%d bb=%L sample=%L"
          length(cv~>shapes) cv~>bBox (car(cv~>shapes))~>bBox))
```
If shapes are there but the GUI is blank, it's LoD culling.

**Workarounds:**
- **Zoom in**: enlarge the visible μm range until each shape spans ≥ 3 screen pixels. For a 600 px wide canvas viewing 200 μm, each μm is ~3 px → 1 μm rects render fine. At 700 μm in the same canvas (~1 μm/px), a 2 μm rect is right at the threshold.
- **Resize the window**: a 1700 px canvas turns the same zoom into ~2.4 μm/px → 2 μm rects become 5 px and survive culling.
- **`envSetVal("layout" "drawTinyObjects" 'boolean t)`** — disables LoD culling (slower redraw but every shape draws).
- **Export instead of screenshot**: print-path renderers (`lePlotImage`, `axlSaveDisplayResource`) bypass LoD and draw every shape regardless of size.

**Probe trick** — if you suspect LoD, drop a single 200 μm × 200 μm rect on the same layer/purpose, screenshot, and confirm the probe is visible while the small shapes are not. We used this to confirm AP-drawing renders fine; only the 2 μm beads were being culled.

## See also

- `references/layout-python-api.md` — Python API reference
