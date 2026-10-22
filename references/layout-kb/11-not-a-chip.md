# A block is not a chip

tags: pad, bond-pad, io-ring, seal-ring, esd, adc

This ADC is a block that will sit inside a larger chip. It does not get a pad frame.

Forbidden on the block, even if a textbook chapter shows them (those chapters are about the
chip):

- bond pads, probe pads, pad openings
- an I/O ring or a seal ring
- ESD clamps on the pins
- fill or logo that exists only because a die needs it

Pins are internal metal pins on the routing grid, named as in the schematic.

Still required, and not pads: diffusion contacts, poly contacts, vias, substrate taps, well
taps, guard rings. "No contact pads" means no bond pads. It does not mean delete contacts.

Source: Hastings and Baker treat the pad frame as chip-level. A block that includes one is
the wrong component, and the pad capacitance is not in the schematic.
