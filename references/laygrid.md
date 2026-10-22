# laygrid: tight template-and-grid layout (LAYGO style)

Hand-placing shapes and guessing spacings is where agent layouts went wrong: one long row
of devices, gaps chosen by trial and error against DRC, unmatched halves. `lib/laygrid.py`
follows LAYGO (Han et al., TCAS-I 2021) and BAG (Chang et al.; BAG3++, Guo et al., OJCAS
2025): **you write the layout generator; laygrid makes each step DRC-clean by
construction and symmetric by pattern.** It is an API, not an auto-placer: every device,
row and route is a call you choose.

| Step | What laygrid does | Source of the numbers |
|---|---|---|
| Templates | instantiate the PDK's own cell/pcell, measure bbox, per-layer extents, pins | Virtuoso, measured |
| Parameters | `fw='1u', l='100n'` are coerced to the pcell's own types and units | pcell defaults vs CDF defaults |
| Spacing | gap between neighbours = smallest that meets every same-layer and two-layer spacing rule at the facing edges, using the width/length spacing table | techfile constraint groups |
| Wells/implants | never push devices apart; if they would sit closer than their spacing, a filler bridges them (LAYGO boundary cells) | techfile layer functions |
| Arrays | `common_centroid` (ABBA..), `interdigitate` (ABAB), `cross_coupled` (2-D), `with_dummies` | - |
| Routing | tracks at pitch = via landing + min spacing; stubs on the pin layer, via stacks found by layer pair, min-area landing patches | techfile via defs, minArea |
| Report | area, aspect, device utilisation, rows | - |

```python
import sys; sys.path.insert(0, '<aspice>/lib')
import laygrid as LG
run = LG.bridge_runner(client)        # live session; or LG.batch_runner(cds_lib=..., workdir=...)
tech = LG.Tech.load(run, 'TECHLIB')   # grid, rules, via defs (a few seconds)
A = LG.Template('PDKLIB', 'nmos_cell', params={'fw': '1u', 'l': '100n', 'fingers': 2})
D = LG.Template('PDKLIB', 'nmos_cell', params={'fw': '1u', 'l': '100n', 'fingers': 1})
tech.measure(run, [A, D])             # one round trip for all templates
L = LG.Layout(tech, 'MYLIB', 'inpair')
row = L.row('M', LG.with_dummies(LG.common_centroid('A', 'B', 2)), {'A': A, 'B': A, 'D': D})
b = L.bbox()
L.via('SUBSTRATE_TAP_VIADEF', (b.cx, b.y0 - 0.35), cutColumns=30)       # tap strip
g = L.tracks('Metal2', 'h', b.y1 + 0.35, 2)                               # gate buses
L.connect([p for i in row if i.name.startswith('MA') for p in i.pin('G')], g[0])
L.connect([p for i in row if i.name.startswith('MB') for p in i.pin('G')], g[1])
print(L.report(area_budget=10))       # area, aspect, utilisation
print(L.centroids({'A': r'MA\d', 'B': r'MB\d'}))   # must coincide
L.write(run, overwrite=True)          # refuses to overwrite unless asked
```

Then `verify.py LIB CELL --steps drc,lvs,...` as usual, and `layout_audit.py` for the
matching checks. Pin names of multi-finger pcells are the PDK's (`G`, `G_gate_2`, `S`,
`S_source_2`, `D` ...): read `tpl.pins` after measuring.

**Live result (gpdk045, 2026-10-21):** an ABBA input pair with dummy ends, substrate
tap strip, gate and drain buses on Metal2 (6 pcells, 21 vias) was DRC clean (PVS) after
two fixes to laygrid itself, with no PDK constants in the code: Metal spacing comes from
the techfile's width/length table, the M1 landing patches from its minArea rule, the
well bridging from its layer functions.

Limits: placement is rectangular (bbox-based); diffusion sharing between neighbours
(abutment) is not done, so rows are slightly longer than a hand-abutted row. Routing
DRC beyond spacing on the track layer (end-of-line, wide-metal spacing to other nets) is
left to `verify --steps drc`.
