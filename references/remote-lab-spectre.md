# Spectre on the UW ECE lab from another computer (Windows, macOS, Linux)

`scripts/lab_run.py` lets an agent on any computer simulate on a lab Linux host
with the real PDKs. The client needs **Python 3.8+ and the OpenSSH client** -
no Cadence, no virtuoso-bridge, no numpy. The lab side reuses aspice's
`lib/cadence.py` from your NFS home, so nothing is installed per host.

What one `run` does:

1. ranks `linux-lab-136..145` by NFS latency (20x write+stat), load and Cadence
   processes, and skips sick hosts (NFS > 0.3 s);
2. stages the deck plus every relative include into one flat folder (CRLF ->
   LF, `../` paths rewritten), uploads it to `/var/tmp/$USER/aspice-remote/<id>`
   on that host (local disk, never NFS);
3. launches Spectre there **detached** (`setsid nohup`), so closing the laptop
   or losing Wi-Fi does not kill a long run;
4. polls, then downloads `result.json`, `spectre.out` and the PSF-ASCII results.

## One-time setup on the Windows machine

Do these with the user; steps 2-3 need their password once.

1. **OpenSSH client.** PowerShell: `ssh -V`. If missing (admin PowerShell):
   `Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0`.
2. **Key login** (the script runs ssh with `BatchMode=yes`, so a password
   prompt is a failure, not a hang):
   ```powershell
   ssh-keygen -t ed25519            # accept defaults
   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh NETID@linux-lab-143.ece.uw.edu "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
   ```
   The lab home is shared NFS, so one key works on every lab host.
3. **ssh config** `%USERPROFILE%\.ssh\config`, so `--user` is never needed:
   ```
   Host linux-lab-*
       User NETID
   Host linux-lab-*.ece.uw.edu
       User NETID
   ```
4. **Network.** On campus it just works. Off campus, if `ssh` times out,
   connect UW's Husky OnNet VPN, or pass `--jump NETID@<a UW host you can reach>`.
5. **The script.** Only `scripts/lab_run.py` is needed on the client. Copy it,
   or the whole skill:
   `scp -r NETID@linux-lab-143.ece.uw.edu:.claude/skills/aspice %USERPROFILE%\.claude\skills\`
   (`references/virtuoso` is a Linux symlink and will be missing; it is only
   for live Virtuoso sessions.)

Check: `python lab_run.py hosts` prints a table of hosts with `ok True`.

## The PDKs on the lab machines

`python lab_run.py pdks --check linux-lab-143` prints these and confirms each
file exists. Paths are lab paths: use them as-is in the deck, they are not uploaded.

| PDK | Include line | Corners | Core devices |
|---|---|---|---|
| gpdk045 | `include "/home/lab.apps/vlsiapps_new/kits/gpdk045_v_6_0/models/spectre/gpdk045.scs" section=tt` | tt ff ss fs sf mc | `nch` `pch` (1.0 V), `_hvt` `_lvt` `18` variants |
| gpdk090 | `include ".../kits/gpdk090_v4.6/models/spectre/gpdk090.scs" section=NN` | NN SS SF FS FF | `gpdk090_nmos1v` `gpdk090_pmos1v` ... |
| gpdk180 | `include ".../kits/gpdk180_v3.3/models/spectre/gpdk.scs" section=NN` | NN SS SF FS FF | `nmos1` `pmos1` (1.8 V) |
| sky130 | `include ".../kits/sky130_release_0.0.1/models/sky130.lib.spice" section=tt` | tt ss ff sf fs | `sky130_fd_pr__nfet_01v8` (subckt: instance with `X`) |
| FreePDK45 | `.include "/home/projects/ee476/common/freepdk45_opencelllibrarypdk45/FreePDK45/ncsu_basekit/models/hspice/tran_models/models_nom/NMOS_VTG.inc"` (+ `PMOS_VTG.inc`) | dirs `models_nom` `_ss` `_ff` | `NMOS_VTG` `PMOS_VTG` (SPICE syntax: inside `simulator lang=spice`) |

- Section names are **case-sensitive**: gpdk090/180 use `NN`, not `tt`. A wrong
  section is a hard error (`No section found with name`).
- Do not include FreePDK45's `hspice_nom.include`: its lines have unterminated
  quotes that HSPICE tolerates and Spectre reads as one broken path.
- An ADE netlist (`.../netlist/input.scs`) already carries the right absolute
  include; run it directly.

## Running

```powershell
python lab_run.py run tb_ota.scs --accuracy high --verify
python lab_run.py run tb_ota.scs --no-wait          # long run: come back later
python lab_run.py status aspice_runs\tb_ota-<id>\job.json
python lab_run.py fetch  aspice_runs\tb_ota-<id>\job.json
```

`--host linux-lab-143` pins a host; `--include file.va` uploads extra files the
deck does not name in an include line; `--spectre-arg +aps` adds Spectre flags;
`--no-psf` fetches only `result.json` (PSF for a long transient can be large).

## Accuracy

| `--accuracy` | Spectre flags | Injected `options` (before the first analysis) |
|---|---|---|
| `netlist` | none | none: exactly what the deck says |
| `high` (default) | `+errpreset=conservative` | `reltol=1e-5 vabstol=1e-7 iabstol=1e-13 gmin=1e-14` |
| `max` | `+errpreset=conservative` | `reltol=1e-6 vabstol=1e-8 iabstol=1e-14 gmin=1e-15` |

The injected statement comes after any `simulatorOptions` in the deck, so it
overrides them.

**`--verify` is how you know the answer is accurate.** It re-runs one level
tighter and compares every saved signal (transients interpolated onto the
first run's time points). The verdict is on node voltages: worst deviation
< 1e-3 of the signal's range means the tolerances are not what limits the
answer. Terminal currents are reported on their own line, because spikes at
switching edges differ between two adaptive time grids. Measured on gpdk045:
a CS stage at the deck's own `reltol=1e-3` was off by **1.3%** in the
transient; `high` agreed with `max` to 3e-4.

Tolerances are not the only accuracy lever. For a transient also:

- set `maxstep` well below the fastest edge you care about;
- for an FFT/ENOB measurement, use `strobeperiod` on coherent sample points;
- do not use Spectre X presets `lx`/`vx` for signal-fidelity work (see the
  `spectre` skill's mode table); `+aps` is fine, `+preset=cx` for sign-off.

A tolerance-converged answer is still only as good as the model: the PDK
corner (`section=`), temperature (`temp=` in options) and Monte Carlo
(`section=mc` + a `montecarlo` analysis) belong in the deck.

## Results

`aspice_runs/<deck>-<id>/` holds:
- `result.json`: `ok`, `errors`, `warnings`, the operating point, a MOS
  `device_table` (region, gm/Id, gm*ro, Vds-Vdsat), the analyses with their
  point and signal counts, and `verify`;
- `spectre.out` and `serve.log`;
- `psf/`.

Read the PSF on the client (needs `pip install numpy`):

```python
import sys; sys.path.insert(0, r'<skill>\lib')
import cadence as CD
an = CD.read_psf_dir(r'aspice_runs\tb_ota-<id>\psf')
ac = an['ac1']; f, vout = ac.x, ac.traces['out']
```

## Many runs (corners, sweeps)

Make one deck per corner and launch each with `--no-wait` and `--host auto`.
The ranking spreads them over idle hosts if each launch waits a few seconds.
Keep it to a few jobs per host. One host running dozens of Spectre jobs
against NFS is what has tipped lab hosts into NFS stalls.

## When it fails

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied (publickey...)` | no key on the lab account | setup step 2 |
| every host times out | off campus | Husky OnNet VPN or `--jump` |
| `NFS slow - do not run Cadence here` | that host's NFS client is stuck | let `--host auto` pick; ask ECE IT to reboot it |
| `ERROR (SFE-868) Cannot open the input file` | include path not on the lab, or a `C:\` path | use lab paths; relative includes are uploaded automatically |
| `PDK_DIR is not defined` | a FreePDK45 file that uses `$PDK_DIR` | `serve` sets it to the lab FreePDK45; check your include path |
| `remote process is gone` | the job crashed or the host rebooted | read `serve.log` from `fetch`; rerun |
