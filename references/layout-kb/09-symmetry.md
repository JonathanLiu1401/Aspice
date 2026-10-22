# Differential symmetry

tags: symmetry, differential, mirror, routing-match

- One axis. Every pair in the block shares it. `layout_audit` warns when the axes disagree.
- Mirror the placement. Route both halves with the same script (same tracks, mirrored), so
  the parasitics match. A via on one side only is a mismatch.
- Do not mirror by copying a finished layout and flipping it in the GUI. The generator
  places both halves. A later hand edit is applied to both halves or to neither.
- The comparator sits on the axis between the two capacitor halves. That is why the CDAC is
  two square-ish arrays, not one wide bar with the comparator at the end.

Source: Hastings, 2nd ed., matching under gradients; this project's phase-2 floorplans.
