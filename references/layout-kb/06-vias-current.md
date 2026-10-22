# Vias, contacts, and current

tags: via, contact, electromigration, current-density, array

A contact connects metal to diffusion or poly. A via connects two metals. Neither is a pad.

- One via is enough only for a tiny signal. A supply, a tail, a sampling-switch path, and
  any net that carries more than one device's current gets an array (2x2 at minimum on
  supplies). Via resistance in series with a switch is a settling error.
- Where the PDK gives a current-density or electromigration limit, width is current divided
  by that limit, then snapped up to a whole number of tracks. Where it does not, supplies
  and tails are several tracks wide and you say so in the layout script. Do not leave a
  supply on minimum width.
- Stacked vias (M1-M2-M3) are arrays at each level, landed on the grid, not a single cut
  offset to "make it fit."
- Diffusion contacts inside a pcell are the pcell's. Do not delete them, and do not confuse
  them with bond pads.

Source: Hastings, 2nd ed., current density, contact and via resistance.
