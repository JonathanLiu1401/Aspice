# Folding a MOSFET

tags: fingers, shared-diffusion, multiplier, orientation, pcell

A wide transistor is N fingers of one pcell, sharing source and drain. The pcell does the sharing. You pick N, the finger width, and the row.

- Finger width stays in the range the PDK models (not a 0.2 um finger and not a single 20 um finger). If the kit's CDF has a recommended maximum finger width, use it.
- A matched pair has the same finger width and the same N, then an ABBA or cross-coupled pattern of those fingers. Do not give one device 4 fingers and the other 2 of twice the width.
- Even N on a differential device keeps the end diffusion the same on both sides once dummies are added.
- All fingers of one device share one orientation. The row generator places them. Do not drop extra fingers in a corner to "use the white space."
- Multiplier (m=) and fingers are different parameters. Read the CDF. One kit's `m` is parallel devices; another's is something else. Set the parameter the pcell actually has.
- Contact the gates on metal at the pitch, and strap sources and drains with enough vias for the current (`06-vias-current.md`).

Source: Hastings, 2nd ed., multi-finger MOS; Clein, *CMOS IC Layout*, device cells. In laygrid this is one `Template` per finger size, then `common_centroid` or `interdigitate`.
