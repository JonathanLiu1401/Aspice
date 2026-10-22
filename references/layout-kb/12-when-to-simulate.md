# When a simulation is allowed

tags: iteration, verify, pex, testbench, generator

Simulation checks a decision you already made. It is not the search.

Allowed:

1. Operating-point check of the schematic before any layout (`scripts/opcheck.py`).
2. One DRC after the generator runs. Fix the script, run again. Do not fix by painting shapes.
3. One post-layout check of a fast leaf (comparator, sampler, clock) when that generator is
   DRC-clean and LVS-clean. Read the worst-net list (`scripts/netbrief.py pex` or verify's
   summary). Change the floorplan or the wire budget in the script and regenerate.
4. One short top-level check (<= 16 conversions, typical corner) after the blocks are in.
5. One long top-level check, both corners, at the end.

Not allowed: a simulation after each wire, a Monte-Carlo to decide a nudge, reading a
waveform into the conversation, re-running an unchanged netlist.

A layout that meets spec after dozens of hand nudges is still a failed method. Rewrite the
generator so the next person can regenerate it.

Source: this project's measured cost (top-level post-layout was 45-80 min before the short
testbench) and Hastings' practice of finishing the floorplan before drawing.
