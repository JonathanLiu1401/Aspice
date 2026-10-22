# opcheck.py: is every transistor where Razavi's method puts it?

A schematic that meets its specs in one simulation can still be wrong: a tail device
4 mV from triode, an input pair built from two different devices, a mirror ratioed from
unequal fingers. Those pass one testbench and fail across PVT, after layout, or in
silicon. **The final schematic must pass `opcheck.py` before layout starts**, and again
after any resize.

```bash
python <aspice>/scripts/opcheck.py tb.scs                     # DC operating point
python <aspice>/scripts/opcheck.py input.scs --at 2n,3.5n     # also at transient times
python <aspice>/scripts/opcheck.py tb.scs --sections tt,ss,ff --temps -40,27,125
python <aspice>/scripts/opcheck.py LIB TB_CELL                # netlist a testbench cell first
```

Input is any Spectre deck, including an ADE `input.scs` (analyses, Monte-Carlo and sweep
blocks are commented out and replaced by `dc` + `info what=oppoint`; relative includes
are made absolute; the original file is never written). Every number comes from Spectre's
own oppoint (`region`, `vds`, `vdsat`, `vth`, `gm`, `gds`, `ids`), mapped back to the
netlist for nets and sizes. No PDK is assumed. Exit code 0 = no FAIL.

| Rule | Level | Razavi |
|---|---|---|
| every analog MOS saturated (triode / off / breakdown fails) | FAIL | Ch. 2-3 |
| weak inversion on an analog device | WARN | Ch. 2 |
| headroom \|Vds\|-\|Vdsat\| >= 100 mV (`--margin`) | WARN | Ch. 9 swing/headroom |
| gm/Id between 5 and 25 /V | WARN | gm/Id design |
| intrinsic gain gm*ro >= 10 for non-diode devices | WARN | Ch. 2, 5 |
| differential pair: identical devices, equal Vgs and Id at the balanced input | WARN | Ch. 4 |
| mirror: same L, same unit finger width, Iout/Iref within 5 % of the size ratio; the Vds difference behind an error is printed | WARN | Ch. 5 |

Switches and logic (triode with Vds ~ 0 and the gate at a rail, or off with Vgs ~ 0) are
recognised and listed, not failed; force with `--switch REGEX`, skip testbench devices
with `--ignore REGEX`. For clocked circuits (comparators, SAR logic, sample switches) the
DC point is meaningless: use `--at` with times inside each phase (e.g. reset and
regeneration of a StrongARM) and judge the analog devices in the phase where they must
amplify.

Mirror ratios: a PDK's `w` is either total width or per finger. When the two devices'
finger counts differ, opcheck takes the convention that agrees with the simulator's own
`betaeff` ratio, so the "size ratio" printed is the one you drew.

## Measured on earlier benchmark designs (5T OTA, gpdk045, 2026-10-21)

All three schematics met the task's gain/bandwidth specs at their test point.

| Design | Finding |
|---|---|
| plain Claude (none t1) | tail M5 saturated by 86 mV; M5 mirrored from a 1-finger reference with different finger width |
| vskill t1 | input pair M1/M2 are different devices (60u/15f vs 180u/45f, Id 0.58 vs 1.70 mA); tail saturated by 5 mV, gm*ro 4.8; mirror 5 % off |
| vsmcp t1 | tail saturated by 4 mV, gm*ro 6.8 |

## How to fix what it reports

- **Triode / small headroom:** raise the node (bias), lower Vdsat (wider W or less
  current), or remove a stacked device. Re-run until headroom >= 100 mV at every corner.
- **gm*ro low:** longer L on current sources and gain devices (not on switches).
- **Pair mismatch:** the two halves must be the same device; a gap at the balanced input
  is a systematic offset from an unbalanced load (mirror Vds) — fix the load.
- **Mirror error:** equalise Vds (cascode or matched loads), lengthen L, ratio by
  multiplying identical unit fingers.

Python: `sys.path.insert(0, '<aspice>/lib'); import opcheck as OP; rep = OP.audit('tb.scs');
print(OP.format_report(rep))`.
