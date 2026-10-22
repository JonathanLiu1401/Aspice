# Stress, LOD, and well proximity

tags: LOD, WPE, well-proximity, stress, dummy-finger, matching

Two transistors with the same W and L still mismatch if one sits against a well edge or at the end of a diffusion run. Nanometer processes make this larger, not smaller.

- Every device in a matched group has the same length of diffusion on both sides (same number of dummy fingers, same spacing to the diffusion end). End dummies exist for this, not for decoration.
- Keep matched devices away from a well edge by the same margin on every side. A device in the middle of the well and a device against the edge are not a pair.
- Do not put a matched pair beside a large unrelated diffusion or a pad ring. There is no pad ring on this block anyway.
- Same orientation. A mirrored pair is allowed when the mirror is the schematic's symmetry axis and both devices have the same neighbor structure. A 90-degree rotate is not matching.
- The generator encodes the dummies and the well margin. Do not add a dummy polygon by hand after DRC.

Source: Hastings, 2nd ed., matching under mechanical stress; Razavi, 2nd ed. (2017), nanometer-device side effects (well proximity, LOD). The techfile's well-spacing rule is the minimum, not the matching rule.
