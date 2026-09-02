"""Describe a circuit once, emit it for Cadence and for local simulation.

The problem this solves. A netlist bound for Cadence carries the PDK's own CDF
parameter names (``fw``, ``fingers``, ``w``) and the PDK's cell names
(``nmos1v``). A netlist you can simulate locally carries SPICE names (``w``,
``l``, ``m``) and whatever model cards you have. They are the same circuit, and
keeping two hand-written copies in step is how a design silently diverges from
what was verified.

So a circuit is described once, in general terms - length, finger width, finger
count - and rendered twice:

    emit_cdl / emit_spectre  ->  PDK names, for import on the Cadence machine
    emit_spice               ->  SPICE names, simulatable here with ngspice

``export_package`` writes both plus a manifest, so what crosses to the Linux box
is a self-describing directory rather than a loose file.

Sizing convention. ``w_f`` is the width of one finger and ``n_f`` the number of
fingers; total width is the product. SPICE gets ``w=w_f`` and ``m=n_f``, because
in SPICE ``w`` is the per-finger width and ``m`` multiplies the device. A PDK
total-width parameter is emitted only when the techmap names one, and is
computed rather than assumed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    'Techmap', 'Device', 'Subckt', 'Design',
    'emit_cdl', 'emit_spectre', 'emit_spice', 'export_package',
    'parse_value', 'format_value', 'DEFAULT_TECHMAP',
]

# General sizing names -> SPICE element parameters. w_tot is deliberately absent:
# emitting the total as SPICE `w` alongside `m` would overcount by n_f.
_SPICE_PARAM = {'l': 'l', 'w_f': 'w', 'n_f': 'm'}

_SI = {'t': 1e12, 'g': 1e9, 'meg': 1e6, 'x': 1e6, 'k': 1e3,
       'm': 1e-3, 'u': 1e-6, 'n': 1e-9, 'p': 1e-12, 'f': 1e-15, 'a': 1e-18}

_NUM = re.compile(r'^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)\s*$')

DEFAULT_TECHMAP = {
    'lib': 'gpdk045',
    'cells': {'nmos': 'nmos1v', 'pmos': 'pmos1v'},
    'params': {'l': 'l', 'w_f': 'fw', 'n_f': 'fingers', 'w_tot': 'w'},
    'defaults': {'l': '1u', 'w_f': '1u', 'n_f': 1},
}


def parse_value(text):
    """'2u' -> 2e-06. Returns None for an expression rather than guessing.

    SPICE unit suffixes are used ('meg' is mega, a bare 'm' is milli). A trailing
    unit letter after the scale ('2uA') is ignored, as SPICE does.
    """
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUM.match(str(text))
    if not m:
        return None
    value, suffix = float(m.group(1)), (m.group(2) or '').lower()
    if not suffix:
        return value
    for scale in ('meg', 't', 'g', 'k', 'm', 'u', 'n', 'p', 'f', 'a', 'x'):
        if suffix.startswith(scale):
            return value * _SI[scale]
    return value          # a bare unit such as 'V' or 'Ohm'


def format_value(value):
    """Render a float compactly, without a unit suffix.

    No suffix is ever emitted: a trailing 'a' or 'f' would be read by ngspice as
    atto or femto, and Spectre reads 'M' as mega where SPICE reads it as milli.
    A plain float means the same thing in every one of these tools.

    12 significant digits, not repr(): scaling 180 by 1e-9 lands on
    1.8000000000000002e-07 in binary floating point, and a netlist full of that
    noise is unreadable and invites a spurious diff. 12 digits is far beyond any
    process tolerance while rendering the intended value exactly.
    """
    if value is None:
        return None
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return '%.12g' % float(value)


@dataclass
class Techmap:
    """The PDK-specific names, kept in one place.

    ``params`` maps the general sizing names (l, w_f, n_f, w_tot) onto the CDF
    parameter names of the target PDK. Include ``w_tot`` only if the PDK has a
    total-width parameter.
    """
    lib: str = DEFAULT_TECHMAP['lib']
    cells: dict = field(default_factory=lambda: dict(DEFAULT_TECHMAP['cells']))
    params: dict = field(default_factory=lambda: dict(DEFAULT_TECHMAP['params']))
    defaults: dict = field(default_factory=lambda: dict(DEFAULT_TECHMAP['defaults']))

    @classmethod
    def load(cls, path):
        """Load a techmap.json. The layout matches KwantaeKim/cdl_gen's."""
        data = json.loads(Path(path).read_text())
        return cls(lib=data.get('lib', ''),
                   cells=data.get('cells', {}),
                   params=data.get('params', {}),
                   defaults=data.get('defaults', {}))

    def save(self, path):
        Path(path).write_text(json.dumps(
            {'lib': self.lib, 'cells': self.cells,
             'params': self.params, 'defaults': self.defaults}, indent=2) + '\n')

    def cell(self, kind):
        """PDK cell name for 'nmos'/'pmos', or the string itself if not a kind."""
        return self.cells.get(kind, kind)

    def pdk_params(self, sizing):
        """General sizing -> PDK CDF names, deriving w_tot when the PDK has one."""
        out = dict(sizing)
        if 'w_tot' in self.params and 'w_tot' not in out:
            w_f, n_f = parse_value(out.get('w_f')), out.get('n_f')
            if w_f is not None and n_f is not None:
                out['w_tot'] = format_value(w_f * int(n_f))
        return {self.params[k]: v for k, v in out.items() if k in self.params}

    def spice_params(self, sizing):
        """General sizing -> SPICE element parameters (w is per finger, m counts)."""
        out = {}
        for key, spice in _SPICE_PARAM.items():
            if key in sizing:
                value = parse_value(sizing[key])
                out[spice] = format_value(value) if value is not None else sizing[key]
        return out


@dataclass
class Device:
    """One transistor (or primitive) instance.

    ``terminals`` is [drain, gate, source, bulk] for a MOSFET, which is the order
    both CDL and SPICE expect. ``kind`` is 'nmos'/'pmos' (resolved through the
    techmap) or an explicit model name. ``sizing`` uses the general names.
    """
    name: str
    kind: str
    terminals: list
    sizing: dict = field(default_factory=dict)

    def cdl_line(self, techmap):
        params = techmap.pdk_params(self.sizing)
        return self._line(techmap.cell(self.kind), params)

    def spice_line(self, techmap, model_override=None):
        params = techmap.spice_params(self.sizing)
        return self._line(model_override or techmap.cell(self.kind), params)

    def _line(self, model, params):
        body = ' '.join(f'{k}={v}' for k, v in params.items())
        return f'{self.name} {" ".join(self.terminals)} {model}' + (f' {body}' if body else '')


@dataclass
class Subckt:
    """A .SUBCKT block: a name, its ports, and the devices inside it."""
    name: str
    ports: list
    devices: list = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def add(self, device):
        if any(d.name == device.name for d in self.devices):
            raise ValueError(
                f'duplicate instance name {device.name!r} in subckt {self.name!r}; '
                f'SPICE and CDL both require instance names to be unique')
        self.devices.append(device)
        return device

    def device(self, name, kind, terminals, **sizing):
        """Build and add a device in one call."""
        return self.add(Device(name=name, kind=kind, terminals=list(terminals),
                               sizing=sizing))

    def nets(self):
        """Every net referenced inside, ports included."""
        seen = list(self.ports)
        for d in self.devices:
            for n in d.terminals:
                if n not in seen:
                    seen.append(n)
        return seen

    def check(self):
        """Structural problems that would waste a trip to the Cadence machine.

        Returns a list of strings. A net touched by exactly one terminal and not
        a port is floating, which is the classic silent mistake.
        """
        problems = []
        counts = {}
        for d in self.devices:
            for n in d.terminals:
                counts[n] = counts.get(n, 0) + 1
            if len(d.terminals) != 4 and d.kind in ('nmos', 'pmos'):
                problems.append(
                    f'{d.name}: {len(d.terminals)} terminals, expected 4 '
                    f'[drain, gate, source, bulk]')
            missing = [k for k in ('l', 'w_f') if k not in d.sizing]
            if missing:
                problems.append(f'{d.name}: no {" or ".join(missing)} given')
        for net, count in counts.items():
            if count == 1 and net not in self.ports:
                problems.append(
                    f'net {net!r} is touched by only one terminal and is not a '
                    f'port: it is floating')
        for port in self.ports:
            if port not in counts:
                problems.append(f'port {port!r} is not connected to any device')
        return problems


@dataclass
class Design:
    """One or more subckts, and the techmap they are rendered through."""
    name: str
    techmap: Techmap = field(default_factory=Techmap)
    subckts: list = field(default_factory=list)

    def subckt(self, name, ports, **params):
        s = Subckt(name=name, ports=list(ports), params=params)
        self.subckts.append(s)
        return s

    def check(self):
        problems = []
        for s in self.subckts:
            problems += [f'{s.name}: {p}' for p in s.check()]
        return problems


def _header(design, target, comment):
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return [f'{comment} Generated by aspice for {target}',
            f'{comment} Design: {design.name}   PDK library: {design.techmap.lib}',
            f'{comment} Generated: {stamp}',
            f'{comment} Cell names and parameters come from the techmap; check them',
            f'{comment} against the PDK before importing.', '']


def emit_cdl(design):
    """CDL for import into Cadence (spiceIn). Uses the PDK's CDF names."""
    lines = _header(design, 'Cadence (CDL)', '*')
    for s in design.subckts:
        head = f'.SUBCKT {s.name} ' + ' '.join(s.ports)
        for k, v in s.params.items():
            head += f' {k}={v}'
        lines.append(head)
        lines += [d.cdl_line(design.techmap) for d in s.devices]
        lines += ['.ENDS', '']
    return '\n'.join(lines)


def emit_spectre(design):
    """Spectre netlist for the Cadence machine. Uses the PDK's CDF names."""
    tm = design.techmap
    lines = _header(design, 'Spectre', '//')
    lines += ['simulator lang=spectre', 'global 0', '']
    for s in design.subckts:
        lines.append(f'subckt {s.name} ' + ' '.join(s.ports))
        if s.params:
            lines.append('parameters ' + ' '.join(f'{k}={v}' for k, v in s.params.items()))
        for d in s.devices:
            params = tm.pdk_params(d.sizing)
            body = ' '.join(f'{k}={v}' for k, v in params.items())
            lines.append(f'    {d.name} ({" ".join(d.terminals)}) {tm.cell(d.kind)}'
                         + (f' {body}' if body else ''))
        lines += [f'ends {s.name}', '']
    return '\n'.join(lines)


def emit_spice(design, models=None, model_names=None, testbench=None):
    """A SPICE deck that runs here on ngspice. Uses w / l / m, not CDF names.

    ``models`` is a model file to .include. ``model_names`` maps 'nmos'/'pmos'
    onto the model names in that file, defaulting to aspice's nch / pch.
    ``testbench`` is appended verbatim, so the same subckt can be exercised
    however the analysis needs.
    """
    names = {'nmos': 'nch', 'pmos': 'pch'}
    names.update(model_names or {})
    lines = [f'* aspice SPICE deck for {design.name} (local verification)']
    if models:
        lines.append(f'.include {str(models).replace(chr(92), "/")}')
    lines.append('')
    for s in design.subckts:
        head = f'.subckt {s.name} ' + ' '.join(s.ports)
        if s.params:
            head += ' params: ' + ' '.join(f'{k}={v}' for k, v in s.params.items())
        lines.append(head)
        for d in s.devices:
            lines.append('  ' + d.spice_line(design.techmap,
                                             model_override=names.get(d.kind, d.kind)))
        lines += ['.ends', '']
    if testbench:
        lines += [testbench.rstrip(), '']
    lines.append('.end')
    return '\n'.join(lines)


def export_package(design, out_dir, models=None, testbench=None, notes=''):
    """Write everything needed to move this design to the Cadence machine.

    Produces <cell>.cdl, <cell>.scs, <cell>.sp, techmap.json, manifest.json and
    a README. Structural problems are recorded in the manifest rather than
    silently dropped, so a floating net is visible before the transfer, not
    after.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = design.name

    written = {}
    for filename, text in (
        (f'{stem}.cdl', emit_cdl(design)),
        (f'{stem}.scs', emit_spectre(design)),
        (f'{stem}.sp', emit_spice(design, models=models, testbench=testbench)),
    ):
        (out / filename).write_text(text)
        written[filename] = len(text)
    design.techmap.save(out / 'techmap.json')

    problems = design.check()
    manifest = {
        'design': design.name,
        'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'generator': 'aspice netlist_export',
        'pdk_library': design.techmap.lib,
        'pdk_cells': design.techmap.cells,
        'cdf_parameter_map': design.techmap.params,
        'subckts': [{'name': s.name, 'ports': s.ports,
                     'devices': len(s.devices),
                     'nets': s.nets()} for s in design.subckts],
        'files': written,
        'structural_warnings': problems,
        'verified_locally': False,
        'notes': notes,
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

    (out / 'README.md').write_text(f"""# {stem} - netlist export

Generated by aspice. Move this whole directory to the Cadence machine.

| File | Purpose |
|---|---|
| `{stem}.cdl` | CDL for `spiceIn` import into a Virtuoso library |
| `{stem}.scs` | Spectre netlist |
| `{stem}.sp` | SPICE deck with SPICE parameter names, for ngspice |
| `techmap.json` | The PDK cell and CDF parameter names used |
| `manifest.json` | What was generated, and any structural warnings |

## Before importing

`{stem}.cdl` and `{stem}.scs` carry the PDK cell names
({', '.join(f'{k} -> {v}' for k, v in design.techmap.cells.items())}) and the CDF
parameter names in `techmap.json`. **Check those against the real PDK first** -
a parameter that reads like a total width in one process is a finger width in
another, and nothing downstream will warn you.

`{stem}.sp` is the same circuit with SPICE names (`w` is the per-finger width and
`m` the finger count). It exists so the design can be simulated before it
travels; it is not the file to import into Virtuoso.

{('## Structural warnings' + chr(10) + chr(10) + chr(10).join('- ' + p for p in problems)) if problems else '## Structural warnings' + chr(10) + chr(10) + 'None.'}

{notes}
""")
    return {'dir': str(out), 'files': sorted(written) + ['techmap.json',
                                                         'manifest.json', 'README.md'],
            'warnings': problems}
