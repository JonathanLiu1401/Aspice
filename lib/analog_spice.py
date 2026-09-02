"""analog_spice - a thin, honest layer over PySpice/ngspice for analog CMOS design.

Import this module *before* anything else touches PySpice: it repairs two
incompatibilities between PySpice 1.4.3 and modern ngspice (>= 33) at import
time, then provides the handful of operations that analog design work actually
needs - operating point with per-device small-signal parameters and region
checks, AC/Bode with gain-bandwidth extraction, noise with per-device
contributions, DC sweeps, transient, and a hand-analysis reconciliation table.

Nothing here hides a simulator result. Every helper returns the raw numbers
alongside anything derived from them.
"""

from __future__ import annotations

import logging
import math
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Environment repair. Must happen before PySpice's shared library is created.
# ---------------------------------------------------------------------------

from PySpice.Spice.NgSpice import Shared as _shared_module
from PySpice.Spice.NgSpice.Shared import NgSpiceShared as _BaseNgSpiceShared
from PySpice.Spice.NgSpice.SimulationType import SIMULATION_TYPE as _SIMULATION_TYPE

# (1) ngspice >= 33 is not in PySpice 1.4.3's version table. The vector-type
#     enum it maps has not changed since ngspice 27, so aliasing newer versions
#     onto the last known table is correct and silences a spurious warning.
for _v in range(33, 80):
    _SIMULATION_TYPE.setdefault(_v, _SIMULATION_TYPE[32])

# (2) PySpice 1.4.3 treats *every* ngspice stderr line that does not begin with
#     "Warning:" as a fatal error. ngspice >= 40 routes benign notes to stderr
#     (e.g. "Note: vg: dc value used for op instead of transient time=0
#     value."), so a perfectly good .ac or .tran run raises NgSpiceCommandError
#     after the analysis has already succeeded. Reclassify notes as non-fatal.
_BENIGN_STDERR = (
    'note:',
    'using ',
    'warning:',
    'supported ngspice version',
)


class _PatchedNgSpiceShared(_BaseNgSpiceShared):
    """NgSpiceShared that does not treat informational notes as errors."""

    @staticmethod
    def _send_char(message_c, ngspice_id, user_data):
        self = _shared_module.ffi.from_handle(user_data)
        message = _shared_module.ffi_string_utf8(message_c)
        prefix, _, content = message.partition(' ')
        if prefix == 'stderr':
            self._stderr.append(content)
            low = content.strip().lower()
            if low == "note: can't find init file.":
                self._spinit_not_found = True
                self._logger.warning('spinit was not found')
            elif not low.startswith(_BENIGN_STDERR):
                self._error_in_stderr = True
                self._logger.error(content)
        else:
            self._stdout.append(content)
            if 'error' in content.lower():
                self._error_in_stdout = True
        return self.send_char(message, ngspice_id)


_shared_module.NgSpiceShared = _PatchedNgSpiceShared
import PySpice.Spice.NgSpice.Simulation as _ngspice_sim_module  # noqa: E402

_ngspice_sim_module.NgSpiceShared = _PatchedNgSpiceShared

# PySpice logs a lot at INFO/WARNING that is noise during normal use.
for _name in ('PySpice', 'PySpice.Spice.NgSpice.Shared'):
    logging.getLogger(_name).setLevel(logging.ERROR)

import numpy as np  # noqa: E402
from PySpice.Spice.Netlist import Circuit, SubCircuit, SubCircuitFactory  # noqa: E402,F401
from PySpice.Unit import *  # noqa: E402,F401,F403

import PySpice.Unit as _unit_module  # noqa: E402

__all__ = [
    'Circuit', 'SubCircuit', 'SubCircuitFactory', 'np',
    'MODELS_DIR', 'tech', 'list_tech',
    'DeviceOp', 'OperatingPoint', 'operating_point',
    'ac', 'bode_summary', 'dc_sweep', 'transient', 'noise', 'noise_table',
    'solve_bias', 'f_t', 'lint_netlist',
    'transfer_function', 'Reconcile', 'db20', 'unwrap_deg',
]
# Re-export the unit symbols (u_V, u_kOhm, u_pF, ...) so that
# `from analog_spice import *` gives a complete working namespace.
__all__ += [n for n in dir(_unit_module) if n.startswith('u_')]

MODELS_DIR = Path(__file__).resolve().parent.parent / 'models'


# ---------------------------------------------------------------------------
# Technology model files
# ---------------------------------------------------------------------------

def list_tech():
    """Return the available technology names, e.g. ['ptm_180nm', ...]."""
    return sorted(p.stem for p in MODELS_DIR.glob('*.lib'))


def tech(name):
    """Resolve a technology name to a model-file path for ``Circuit.include``.

    Accepts 'ptm_180nm', '180nm', or '180'. Every shipped library defines the
    same two model names, ``nch`` and ``pch``, so changing technology means
    changing only this one call.
    """
    candidates = list_tech()
    key = str(name).lower().removesuffix('.lib')
    for cand in candidates:
        if key in (cand, cand.removeprefix('ptm_')):
            return _spice_path(MODELS_DIR / (cand + '.lib'))
    # loose match: '180' -> 'ptm_180nm'
    loose = [c for c in candidates if key in c]
    if len(loose) == 1:
        return _spice_path(MODELS_DIR / (loose[0] + '.lib'))
    raise ValueError(
        f'unknown technology {name!r}; available: {candidates}'
        + (f' (ambiguous: {loose})' if loose else '')
    )


def _spice_path(path):
    """ngspice on Windows is happiest with forward slashes."""
    return str(Path(path)).replace(os.sep, '/')


# ---------------------------------------------------------------------------
# Operating point
# ---------------------------------------------------------------------------

_SHOW_LINE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(-?[\d.]+(?:[eE][-+]?\d+)?)\s*$')


class DeviceOp:
    """Bias point and small-signal parameters of one MOS device.

    ``raw`` holds every parameter ngspice reported for the device, verbatim.
    The named properties are conveniences over ``raw``; when a model does not
    report a parameter the property is ``None`` rather than a guess.

    Note on capacitances: BSIM4 (level 54) reports them, BSIM3 (level 49) does
    not. ngspice reports BSIM4 capacitances as signed Jacobian entries, so
    ``cgs`` is typically negative; the ``*_abs`` properties give magnitudes.
    """

    def __init__(self, name, raw):
        self.name = name
        self.raw = raw

    def _get(self, *keys):
        for k in keys:
            if k in self.raw:
                return self.raw[k]
        return None

    # --- bias point ---
    @property
    def id(self):
        return self._get('id')

    @property
    def vgs(self):
        return self._get('vgs')

    @property
    def vds(self):
        return self._get('vds')

    @property
    def vbs(self):
        return self._get('vbs')

    @property
    def vth(self):
        return self._get('vth')

    @property
    def vdsat(self):
        return self._get('vdsat')

    @property
    def vov(self):
        """Overdrive |Vgs| - |Vth|. Long-channel notion; compare with vdsat."""
        if self.vgs is None or self.vth is None:
            return None
        return abs(self.vgs) - abs(self.vth)

    # --- small signal ---
    @property
    def gm(self):
        return self._get('gm')

    @property
    def gmb(self):
        return self._get('gmbs', 'gmb')

    @property
    def gds(self):
        return self._get('gds')

    @property
    def ro(self):
        g = self.gds
        return None if not g else 1.0 / g

    @property
    def gm_id(self):
        """Transconductance efficiency gm/ID (1/V) - the gm/ID design knob."""
        if self.gm is None or not self.id:
            return None
        return self.gm / abs(self.id)

    @property
    def intrinsic_gain(self):
        """gm*ro, the maximum voltage gain of a single device."""
        if self.gm is None or self.ro is None:
            return None
        return self.gm * self.ro

    # --- capacitances (BSIM4 only) ---
    @property
    def cgs(self):
        return self._get('cgs')

    @property
    def cgd(self):
        return self._get('cgd')

    @property
    def cgg(self):
        return self._get('cgg')

    @property
    def cgs_abs(self):
        return None if self.cgs is None else abs(self.cgs)

    @property
    def cgd_abs(self):
        return None if self.cgd is None else abs(self.cgd)

    # There is deliberately no `ft` property here. ngspice reports BSIM4's
    # *intrinsic* charge-model capacitances, which exclude the gate overlap
    # terms; at 45 nm the omission is large enough that gm/(2*pi*Cgg)
    # overestimates fT by more than 2x against a measured current gain.
    # Use `f_t(circuit, ...)`, which measures |h21| = 1 directly.

    # --- region ---
    @property
    def region(self):
        """'cutoff', 'triode', 'saturation', or 'unknown'.

        Uses the model's own Vdsat, which is the honest test for short-channel
        devices where Vov is not Vdsat. Inversion level is reported separately
        by :attr:`inversion`: a weakly-inverted device can still be saturated,
        so the two questions must not be conflated.
        """
        if self.id is None or self.vds is None:
            return 'unknown'
        if abs(self.id) < 1e-12:
            return 'cutoff'
        if self.vdsat is None:
            return 'unknown'
        return 'saturation' if abs(self.vds) >= abs(self.vdsat) else 'triode'

    @property
    def inversion(self):
        """'weak', 'moderate' or 'strong', from the overdrive.

        Boundaries are the usual rules of thumb (Vov < 0 weak, < ~100 mV
        moderate); they are indicative, not model-exact. gm/ID is the more
        reliable indicator: ~25-30 1/V in weak inversion, falling towards
        2/Vov in strong inversion.
        """
        vov = self.vov
        if vov is None:
            return 'unknown'
        if vov < 0:
            return 'weak'
        return 'moderate' if vov < 0.1 else 'strong'

    @property
    def sat_margin(self):
        """|Vds| - |Vdsat|. Positive means saturated; this is the headroom."""
        if self.vds is None or self.vdsat is None:
            return None
        return abs(self.vds) - abs(self.vdsat)

    def __repr__(self):
        def f(x, unit='', scale=1.0, nd=4):
            return 'n/a' if x is None else f'{x * scale:.{nd}g}{unit}'
        return (f'<{self.name} {self.region} '
                f'Id={f(self.id, "uA", 1e6)} Vgs={f(self.vgs, "V")} '
                f'Vds={f(self.vds, "V")} Vdsat={f(self.vdsat, "V")} '
                f'gm={f(self.gm, "uS", 1e6)} ro={f(self.ro, "k", 1e-3)}>')


class OperatingPoint:
    """DC operating point: node voltages, source branch currents, device state."""

    def __init__(self, nodes, branches, devices):
        self.nodes = nodes
        self.branches = branches
        self.devices = devices

    def __getitem__(self, key):
        if key in self.nodes:
            return self.nodes[key]
        if key in self.devices:
            return self.devices[key]
        if key in self.branches:
            return self.branches[key]
        raise KeyError(key)

    def v(self, node):
        """Node voltage. Ground is 0 by definition."""
        if str(node) in ('0', 'gnd'):
            return 0.0
        return self.nodes[str(node)]

    def i(self, source):
        """Current *into* the named independent voltage source's + terminal.

        ngspice's sign convention: current flowing from + to - inside the
        source. Supply current drawn by the circuit is therefore ``-i(vdd)``.
        """
        key = str(source).lower()
        for k, val in self.branches.items():
            if k.lower().strip('i()#branch') == key or k.lower() == key:
                return val
        return self.branches[key]

    def check_saturation(self, *devices, verbose=True):
        """Assert the named devices (default: all) are saturated.

        Returns (ok, list_of_problem_strings). Prints a per-device table when
        ``verbose``. This is the single most common source of wrong answers in
        textbook problems, so it is worth running every time.
        """
        names = list(devices) if devices else list(self.devices)
        problems = []
        rows = []
        for n in names:
            d = self.devices[n]
            ok = d.region == 'saturation'
            if not ok:
                problems.append(
                    f'{n} is in {d.region} '
                    f'(|Vds|={_fmt(d.vds)} vs |Vdsat|={_fmt(d.vdsat)})'
                )
            rows.append((n, d.region, d.vds, d.vdsat, d.sat_margin))
        if verbose:
            print(f'{"device":<10}{"region":<14}{"Vds":>10}{"Vdsat":>10}{"margin":>10}')
            for n, reg, vds, vdsat, m in rows:
                flag = '' if reg == 'saturation' else '   <-- NOT SATURATED'
                print(f'{n:<10}{reg:<14}{_fmt(vds):>10}{_fmt(vdsat):>10}'
                      f'{_fmt(m):>10}{flag}')
        return (not problems), problems

    def table(self, *devices):
        """Print the small-signal parameter table for the named devices."""
        names = list(devices) if devices else list(self.devices)
        hdr = (f'{"device":<10}{"region":<13}{"Id[uA]":>10}{"Vgs[V]":>9}'
               f'{"Vds[V]":>9}{"Vdsat":>8}{"gm[uS]":>10}{"gmb[uS]":>9}'
               f'{"ro[kOhm]":>10}{"gm/Id":>8}{"gm*ro":>8}')
        print(hdr)
        print('-' * len(hdr))
        for n in names:
            d = self.devices[n]
            print(f'{n:<10}{d.region:<13}'
                  f'{_fmt(d.id, 1e6, 6):>10}{_fmt(d.vgs, 1, 4):>9}'
                  f'{_fmt(d.vds, 1, 4):>9}{_fmt(d.vdsat, 1, 4):>8}'
                  f'{_fmt(d.gm, 1e6, 6):>10}{_fmt(d.gmb, 1e6, 5):>9}'
                  f'{_fmt(d.ro, 1e-3, 5):>10}{_fmt(d.gm_id, 1, 3):>8}'
                  f'{_fmt(d.intrinsic_gain, 1, 3):>8}')

    def __repr__(self):
        return (f'<OperatingPoint {len(self.nodes)} nodes, '
                f'{len(self.devices)} devices>')


def _fmt(x, scale=1.0, nd=4):
    return 'n/a' if x is None else f'{x * scale:.{nd}g}'


def _mos_names(circuit):
    """Names of MOSFET instances in the circuit, lower-cased as ngspice sees them."""
    names = []
    for element in circuit.elements:
        name = str(element.name)
        if name[0].upper() == 'M':
            names.append(name.lower())
    return names


def _parse_show(text):
    """Parse ngspice's ``show <dev> : all`` two-column output into a dict."""
    out = {}
    for line in text.splitlines():
        m = _SHOW_LINE.match(line)
        if m:
            try:
                out[m.group(1).lower()] = float(m.group(2))
            except ValueError:
                pass
    return out


def _device_ops(shared, names):
    """Read each device's parameters at full double precision.

    ``show`` is used only to *discover* which parameters a given model
    publishes (it differs between level 1, BSIM3 and BSIM4), because its
    printed output is truncated to about 6 significant figures. Each
    discovered parameter is then copied into an ngspice vector with ``let``
    and read back as a raw double, which costs no extra simulation.
    """
    devices = {}
    plot = shared.plot_names[0] if shared.plot_names else None
    for name in names:
        try:
            text = shared.exec_command(f'show {name} : all')
        except Exception:
            continue
        raw = _parse_show(text)
        if not raw:
            continue
        if plot is not None:
            raw = dict(raw)
            for param in list(raw):
                vector = f'zz_{name}_{param}'
                try:
                    shared.exec_command(f'let {vector} = @{name}[{param}]')
                except Exception:
                    continue  # parameter is not @-accessible; keep the show value
                try:
                    data = shared.plot(None, plot)[vector]._data
                    raw[param] = float(np.asarray(data).ravel()[0].real)
                except Exception:
                    pass
        devices[name] = DeviceOp(name, raw)
    return devices


# A bare 'A' or 'F' immediately after a number is read by ngspice as the
# *scale factor* atto (1e-18) or femto (1e-15), not as an ampere or farad unit.
# PySpice's `100e-6 @ u_A` emits "0.0001A", which ngspice silently evaluates as
# 1e-22 A. The analysis then converges to a wrong answer with no warning at all.
_BAD_SUFFIX = re.compile(
    r'(?<![A-Za-z0-9_.])\d[\d.]*(?:[eE][-+]?\d+)?[AaFf][A-Za-z]*(?![\w.])')
_SKIP_LINE = re.compile(r'^\s*[*.]|^\s*\.?(include|lib|model)\b', re.IGNORECASE)


def lint_netlist(circuit):
    """Return a list of lines whose numeric literals ngspice will misread.

    Currently detects the silent atto/femto suffix trap. Raising early beats
    debugging a circuit that simulated 'successfully' with a current source
    18 orders of magnitude too small.
    """
    problems = []
    for line in str(circuit).splitlines():
        if _SKIP_LINE.match(line):
            continue
        for match in _BAD_SUFFIX.finditer(line):
            token = match.group(0)
            scale = 'atto (1e-18)' if token[-1].lower().startswith('a') else 'femto (1e-15)'
            problems.append(
                f'{line.strip()!r}: ngspice reads {token!r} using the '
                f'{scale} scale factor, not as a unit. Use a plain float '
                f'(0.0001), or a scaled unit such as @u_uA / @u_pF.')
    return problems


def _simulator(circuit, temperature=27, nominal_temperature=27, **kwargs):
    problems = lint_netlist(circuit)
    if problems:
        raise ValueError('netlist would be silently misread by ngspice:\n  '
                         + '\n  '.join(problems))
    return circuit.simulator(temperature=temperature,
                             nominal_temperature=nominal_temperature,
                             **kwargs)


def operating_point(circuit, temperature=27, nominal_temperature=27, **kwargs):
    """Run .op and return an :class:`OperatingPoint`.

    Device small-signal parameters are read back with ngspice's ``show``
    command, which works for every MOS model (level 1/3/49/54) without needing
    to know in advance which parameters that model publishes.
    """
    sim = _simulator(circuit, temperature, nominal_temperature, **kwargs)
    analysis = sim.operating_point()
    nodes = {str(k): float(np.asarray(v)[0]) for k, v in analysis.nodes.items()}
    branches = {str(k): float(np.asarray(v)[0]) for k, v in analysis.branches.items()}
    devices = _device_ops(sim._ngspice_shared, _mos_names(circuit))
    return OperatingPoint(nodes, branches, devices)


# ---------------------------------------------------------------------------
# AC / Bode
# ---------------------------------------------------------------------------

def db20(x):
    """20*log10|x|, safe at zero."""
    a = np.abs(np.asarray(x, dtype=complex))
    return 20 * np.log10(np.where(a == 0, 1e-300, a))


def unwrap_deg(x):
    """Phase of a complex array in degrees, unwrapped."""
    return np.degrees(np.unwrap(np.angle(np.asarray(x, dtype=complex))))


def ac(circuit, start_frequency=1, stop_frequency=1e12, points_per_decade=20,
       variation='dec', temperature=27, nominal_temperature=27, **kwargs):
    """Run .ac and return ``(freq, {node: complex_array})``.

    The AC source must declare an AC magnitude, e.g.
    ``circuit.SinusoidalVoltageSource('in', 'in', gnd, dc_offset=..., ac_magnitude=1@u_V)``
    or ``circuit.V('in', 'in', gnd, 'DC 0.9 AC 1')``. With ac_magnitude = 1 V
    the node arrays *are* the transfer functions.
    """
    sim = _simulator(circuit, temperature, nominal_temperature)
    analysis = sim.ac(start_frequency=start_frequency,
                      stop_frequency=stop_frequency,
                      number_of_points=points_per_decade,
                      variation=variation, **kwargs)
    freq = np.asarray(analysis.frequency, dtype=float)
    data = {str(k): np.asarray(v, dtype=complex) for k, v in analysis.nodes.items()}
    for k, v in analysis.branches.items():
        data[str(k)] = np.asarray(v, dtype=complex)
    return freq, data


def bode_summary(freq, h, ref_gain=None):
    """Extract the numbers a Bode plot is usually drawn to obtain.

    Returns a dict with ``dc_gain`` (linear and dB), ``f_3db``, ``ugb``
    (unity-gain bandwidth), ``phase_margin`` (deg, at the unity-gain crossing),
    and ``gbw`` (dc_gain * f_3db). Values that the sweep does not bracket come
    back as ``None`` rather than an extrapolation.
    """
    freq = np.asarray(freq, dtype=float)
    h = np.asarray(h, dtype=complex)
    mag = np.abs(h)
    mag_db = db20(h)
    phase = unwrap_deg(h)

    dc_gain = float(mag[0]) if ref_gain is None else float(ref_gain)
    out = {
        'dc_gain': dc_gain,
        'dc_gain_db': 20 * math.log10(dc_gain) if dc_gain > 0 else None,
        'f_3db': _crossing(freq, mag_db, mag_db[0] - 3.0, falling=True),
        'ugb': _crossing(freq, mag_db, 0.0, falling=True),
        'phase_margin': None,
        'gbw': None,
    }
    if out['ugb'] is not None:
        ph = float(np.interp(math.log10(out['ugb']), np.log10(freq), phase))
        out['phase_at_ugb'] = ph
        out['phase_margin'] = 180.0 + ph
    if out['f_3db'] is not None:
        out['gbw'] = dc_gain * out['f_3db']
    return out


def _crossing(freq, y, level, falling=True):
    """First frequency where ``y`` crosses ``level``, log-interpolated."""
    y = np.asarray(y, dtype=float)
    sign = np.sign(y - level)
    for i in range(len(y) - 1):
        if sign[i] == 0:
            return float(freq[i])
        if sign[i] != sign[i + 1] and sign[i + 1] != 0:
            if falling and y[i] < y[i + 1]:
                continue
            if not falling and y[i] > y[i + 1]:
                continue
            lf0, lf1 = math.log10(freq[i]), math.log10(freq[i + 1])
            t = (level - y[i]) / (y[i + 1] - y[i])
            return float(10 ** (lf0 + t * (lf1 - lf0)))
    return None


# ---------------------------------------------------------------------------
# DC sweep, transient, transfer function
# ---------------------------------------------------------------------------

def dc_sweep(circuit, temperature=27, nominal_temperature=27, **sweep):
    """Run .dc. Pass the swept source as a keyword, e.g. ``Vg=slice(0, 1.8, 0.01)``.

    Returns ``(sweep_values, analysis)``; ``analysis`` is the raw PySpice object
    so node arrays are reachable as ``analysis['out']`` and branch currents as
    ``analysis.branches['vd']``.
    """
    sim = _simulator(circuit, temperature, nominal_temperature)
    analysis = sim.dc(**sweep)
    return np.asarray(analysis.sweep, dtype=float), analysis


def transient(circuit, step_time, end_time, temperature=27,
              nominal_temperature=27, **kwargs):
    """Run .tran. Returns ``(time, analysis)``."""
    sim = _simulator(circuit, temperature, nominal_temperature)
    analysis = sim.transient(step_time=step_time, end_time=end_time, **kwargs)
    return np.asarray(analysis.time, dtype=float), analysis


def transfer_function(circuit, output, source, temperature=27,
                      nominal_temperature=27):
    """Run .tf. Returns ``{'gain':, 'input_impedance':, 'output_impedance':}``.

    ``output`` is a node expression such as ``'v(out)'``; ``source`` names an
    independent source, e.g. ``'vin'``.
    """
    sim = _simulator(circuit, temperature, nominal_temperature)
    sim.transfer_function(output, source)
    shared = sim._ngspice_shared
    result = {}
    for plot_name in shared.plot_names:
        if not plot_name.startswith('tf'):
            continue
        for name, vec in shared.plot(None, plot_name).items():
            value = float(np.asarray(vec._data)[0].real)
            low = name.lower()
            if 'transfer_function' in low:
                result['gain'] = value
            elif 'input_impedance' in low:
                result['input_impedance'] = value
            elif 'output_impedance' in low:
                result['output_impedance'] = value
    return result


# ---------------------------------------------------------------------------
# Biasing and fT
# ---------------------------------------------------------------------------

def solve_bias(build, target_node, target_value, low, high,
               tol=1e-6, max_iter=60):
    """Find the source value that puts ``target_node`` at ``target_value``.

    ``build(x)`` must return a fresh :class:`Circuit` biased with the trial
    value ``x``. Bisection on ``[low, high]``; the node voltage must be
    monotonic in ``x`` over that interval, which it is for every ordinary
    bias-setting knob.

    Returns ``(x, operating_point)`` at the solution. Raises ValueError when
    the bracket does not straddle the target, which is the honest failure -
    it means the requested output is not reachable over that range.

    Biasing a gain stage to a specific output level is the most common setup
    step in a textbook problem and the easiest to get subtly wrong by hand.
    """
    def error_at(x):
        op = operating_point(build(x))
        return op.v(target_node) - target_value, op

    err_low, _ = error_at(low)
    err_high, _ = error_at(high)
    if err_low == 0:
        return low, error_at(low)[1]
    if err_high == 0:
        return high, error_at(high)[1]
    if err_low * err_high > 0:
        raise ValueError(
            f'{target_node} does not cross {target_value} for the bias range '
            f'[{low}, {high}]: it moves from {target_value + err_low:.6g} to '
            f'{target_value + err_high:.6g}. Widen the range or check the topology.'
        )
    op = None
    for _ in range(max_iter):
        mid = 0.5 * (low + high)
        err, op = error_at(mid)
        if abs(err) < tol or (high - low) < tol * 1e-3:
            return mid, op
        if err * err_low > 0:
            low, err_low = mid, err
        else:
            high = mid
    return 0.5 * (low + high), op


def f_t(circuit, gate_source, drain_source, start_frequency=1e6,
        stop_frequency=1e14, points_per_decade=50):
    """Measure the transit frequency fT as the frequency where |h21| = 1.

    This is the definitive measurement: the small-signal current gain of the
    device with its drain AC-shorted. Do NOT compute fT from the capacitances
    reported by ``show`` - ngspice publishes BSIM4's intrinsic charge-model
    capacitances only, which omit the gate overlap terms and overestimate fT
    by more than 2x at 45 nm.

    The circuit must drive the gate from ``gate_source`` (with
    ``ac_magnitude=1``) and hold the drain at a DC voltage source named
    ``drain_source``, which acts as the AC short. Returns fT in Hz, or None if
    the sweep does not bracket the unity crossing.
    """
    sim = _simulator(circuit)
    analysis = sim.ac(start_frequency=start_frequency,
                      stop_frequency=stop_frequency,
                      number_of_points=points_per_decade, variation='dec')
    freq = np.asarray(analysis.frequency, dtype=float)
    i_gate = np.asarray(analysis.branches[gate_source.lower()], dtype=complex)
    i_drain = np.asarray(analysis.branches[drain_source.lower()], dtype=complex)
    h21_db = db20(i_drain / i_gate)
    return _crossing(freq, h21_db, 0.0, falling=True)


# ---------------------------------------------------------------------------
# Noise
# ---------------------------------------------------------------------------

def noise(circuit, output_node, source, start_frequency, stop_frequency,
          points_per_decade=10, ref_node=None, variation='dec',
          temperature=27, nominal_temperature=27):
    """Run .noise and return spectra plus the per-device breakdown.

    Returns a dict with ``freq``, ``onoise`` and ``inoise`` (V/sqrt(Hz),
    referred to output and input), ``contributions`` (per-generator output
    noise density, e.g. ``'onoise_m1_id'``, ``'onoise_r1_thermal'``), and
    ``integrated_onoise``/``integrated_inoise`` (RMS over the swept band,
    integrated on the power spectrum).

    Contributions are output-referred *densities*; they add in power, so
    ``sqrt(sum(c**2))`` reproduces ``onoise``.
    """
    sim = _simulator(circuit, temperature, nominal_temperature)
    sim.noise(output_node=output_node,
              ref_node=circuit.gnd if ref_node is None else ref_node,
              src=source, variation=variation, points=points_per_decade,
              start_frequency=start_frequency, stop_frequency=stop_frequency,
              points_per_summary=1)
    shared = sim._ngspice_shared
    # ngspice makes two plots: noise1 holds the spectral densities, noise2 the
    # band-integrated totals. Device contributions are named with dots
    # ("onoise.m1.id") while resistor ones use underscores ("onoise_r1_thermal");
    # normalise to underscores so both can be handled uniformly.
    spectra, totals = {}, {}
    for plot_name in shared.plot_names:
        if not plot_name.startswith('noise'):
            continue
        target = totals if plot_name == 'noise2' else spectra
        for name, vec in shared.plot(None, plot_name).items():
            key = name.lower().replace('.', '_')
            target[key] = np.asarray(vec._data, dtype=float).real

    freq = spectra.get('frequency')
    onoise = spectra.get('onoise_spectrum')
    inoise = spectra.get('inoise_spectrum')
    contributions = {
        k: v for k, v in spectra.items()
        if k.startswith('onoise_') and k != 'onoise_spectrum'
    }
    result = {
        'freq': freq,
        'onoise': onoise,
        'inoise': inoise,
        'contributions': contributions,
        'integrated_contributions': {
            k: v for k, v in totals.items()
            if k.startswith('onoise_total_') or k.startswith('onoise_total')
            and k != 'onoise_total'},
        'raw': {**spectra, **totals},
    }
    # Prefer ngspice's own integration over re-integrating the spectrum.
    for key, vec_name in (('integrated_onoise', 'onoise_total'),
                          ('integrated_inoise', 'inoise_total')):
        if vec_name in totals:
            result[key] = float(np.asarray(totals[vec_name]).ravel()[0])
    if 'integrated_onoise' not in result and freq is not None and len(freq) > 1:
        for key, spectrum in (('integrated_onoise', onoise),
                              ('integrated_inoise', inoise)):
            if spectrum is not None:
                result[key] = float(np.sqrt(np.trapezoid(spectrum ** 2, freq)))
    return result


def noise_table(result, top=12, index=0):
    """Print the dominant noise contributors at one frequency of the sweep.

    ``index`` selects the frequency point (0 = the lowest swept frequency).
    Contributions are output-referred densities and add in power, so the
    percentages are of noise *power* and sum to 100.
    """
    contributions = result['contributions']
    if not contributions:
        print('no per-device contributions returned '
              '(ngspice omits them for a single-frequency sweep - '
              'sweep at least two points)')
        return
    total = result['onoise']
    # Drop the per-device roll-up rows (e.g. 'onoise_m1', 'onoise_r1'), which
    # duplicate the sum of that device's own generators listed beside them.
    leaves = {k: v for k, v in contributions.items() if k.count('_') >= 2}
    use = leaves or contributions
    ranked = sorted(use.items(), key=lambda kv: -abs(np.asarray(kv[1]).ravel()[index]))
    denom = float(total[index]) ** 2 if total is not None else None
    freq = result.get('freq')
    if freq is not None:
        print(f'at f = {freq[index]:.4g} Hz')
    print(f'{"generator":<28}{"V/sqrt(Hz)":>14}{"% of power":>12}')
    for name, values in ranked[:top]:
        value = float(np.asarray(values).ravel()[index])
        share = f'{100 * value ** 2 / denom:9.2f} %' if denom else '      n/a'
        print(f'{name:<28}{value:14.4e}{share:>12}')


# ---------------------------------------------------------------------------
# Hand-analysis reconciliation
# ---------------------------------------------------------------------------

class Reconcile:
    """Accumulate simulated-vs-hand comparisons and print a verdict table.

    A textbook answer is only trustworthy when the hand model and the simulator
    agree for a reason. Record each quantity both ways, then print the table -
    disagreements are the interesting result, not a failure to hide.
    """

    def __init__(self, title='', default_tol=0.05):
        self.title = title
        self.default_tol = default_tol
        self.rows = []

    def add(self, quantity, spice, hand, tol=None, unit=''):
        tol = self.default_tol if tol is None else tol
        if hand in (0, None) or spice is None:
            rel = None
        else:
            rel = abs(spice - hand) / abs(hand)
        self.rows.append((quantity, spice, hand, rel, tol, unit))
        return rel

    @property
    def ok(self):
        return all(r is not None and r <= tol for _, _, _, r, tol, _ in self.rows)

    def report(self):
        if self.title:
            print(f'\n{self.title}')
        hdr = f'{"quantity":<22}{"simulated":>15}{"hand":>15}{"rel.err":>10}  verdict'
        print(hdr)
        print('-' * (len(hdr) + 4))
        for quantity, spice, hand, rel, tol, unit in self.rows:
            if rel is None:
                verdict, rel_s = '?', '   n/a'
            elif rel <= tol:
                verdict, rel_s = 'agree', f'{100 * rel:8.3f}%'
            else:
                verdict, rel_s = f'DISAGREE (>{100 * tol:g}%)', f'{100 * rel:8.3f}%'
            label = f'{quantity}{f" [{unit}]" if unit else ""}'
            print(f'{label:<22}{_sig(spice):>15}{_sig(hand):>15}{rel_s:>10}  {verdict}')
        print('-' * (len(hdr) + 4))
        print('ALL AGREE' if self.ok else 'MISMATCH - reconcile before trusting either number')
        return self.ok


def _sig(x, nd=6):
    return 'n/a' if x is None else f'{x:.{nd}g}'
