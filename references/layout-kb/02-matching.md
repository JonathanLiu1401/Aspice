# Matching: centroid, interdigitation, dummies

tags: matching, common-centroid, ABBA, dummy, interdigitate, mirror

Matched devices are identical instances of one PDK pcell: same master, W, L, fingers,
orientation. A ratio is a count of unit fingers, never a different finger width.

- A pair that must reject a gradient is common-centroid: ABBA in one row, or a 2-D
  cross-coupled ABAB / BABA. The centroids coincide. `laygrid.common_centroid` and
  `laygrid.cross_coupled` build the pattern; you choose which pattern.
- A ratioed mirror is interdigitated unit fingers (ABAB), not two transistors of different width.
- Every matched row starts and ends with a dummy, tied off (gate to source or to a rail),
  so every real unit has the same neighbours. Capacitor arrays get the same ring of dummies.
- One symmetry axis for the whole differential circuit. Both halves use the same orientation
  family (mirror, not a rotated copy that sees a different stress).

Source: Hastings, 2nd ed. (2006); Baker, *CMOS Circuit Design, Layout, and Simulation*.
Check: `scripts/layout_audit.py --pairs-from tb.scs` and `--groups`.
