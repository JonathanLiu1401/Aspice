"""Design charts from a real PDK: the curves you size a circuit from.

Every function here runs the simulator against whatever model you point it at
and writes a PNG. The charts are the ones an analog designer actually reads:

    iv_family     Id vs Vds at several Vgs - saturation onset, output resistance
    transfer      Id and gm vs Vgs - threshold, transconductance
    gm_id_chart   the gm/ID design chart: gm/ID and ID/W against overdrive,
                  plus intrinsic gain, which is how a device is sized without
                  guessing at a W and iterating
    bode          magnitude and phase, with the -3 dB point and unity-gain
                  crossing marked
    transient     time-domain waveforms
    sweep_metric  any measured quantity against any swept design variable

Matplotlib runs headless (Agg), so these work over SSH with no display.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from analog_spice import (Circuit, u_V, u_Ohm, operating_point, dc_sweep,
                          bode_summary, db20, unwrap_deg, _spice_path)

__all__ = ['iv_family', 'transfer', 'gm_id_chart', 'bode', 'transient',
           'sweep_metric', 'style']

_C = ['#2b6cb0', '#c05621', '#2f855a', '#6b46c1', '#b83280', '#00796b',
      '#8d6e63', '#455a64']


def style(ax, title=None, xlabel=None, ylabel=None, legend=False):
    """One consistent look: light grid, no top/right spine, readable labels."""
    if title:
        ax.set_title(title, fontsize=11, pad=8)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, alpha=0.25, linewidth=0.6)
    ax.tick_params(labelsize=8)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    if legend:
        ax.legend(fontsize=8, frameon=False)
    return ax


def _device(models, model, w, l, kind='nmos', section=None):
    """A single MOSFET with its own drain and gate supplies, ready to sweep."""
    c = Circuit('dev')
    if section:
        c.raw_spice += f'.lib {_spice_path(models)} {section}\n'
    else:
        c.include(_spice_path(models))
    c.V('d', 'd', c.gnd, 0 @ u_V)
    c.V('g', 'g', c.gnd, 0 @ u_V)
    if kind == 'nmos':
        c.MOSFET(1, 'd', 'g', c.gnd, c.gnd, model=model, w=w, l=l)
    else:
        c.MOSFET(1, 'd', 'g', c.gnd, c.gnd, model=model, w=w, l=l)
    return c


def _sweep_id(models, model, w, l, vgs, vds_stop, step, kind, section):
    """Id over a Vds sweep at one Vgs."""
    c = _device(models, model, w, l, kind, section)
    c.Vg.dc_value = vgs @ u_V
    sweep, analysis = dc_sweep(c, Vd=slice(0, vds_stop, step))
    return np.asarray(sweep, dtype=float), -np.asarray(analysis.branches['vd'],
                                                       dtype=float)


def iv_family(models, model='nch', w=10e-6, l=0.18e-6, vgs_list=None,
              vdd=1.8, points=200, kind='nmos', section=None,
              out='iv_family.png', title=None):
    """Id vs Vds at several Vgs, with the saturation boundary marked.

    The dashed curve is Vds = Vdsat taken from the model itself, so the
    saturation boundary shown is the model's own, not the long-channel
    Vov approximation.
    """
    vgs_list = vgs_list or [round(v, 3) for v in np.linspace(0.4 * vdd, vdd, 5)]
    fig, ax = plt.subplots(figsize=(7, 4.6), dpi=140)

    boundary = []
    for i, vgs in enumerate(vgs_list):
        vds, idd = _sweep_id(models, model, w, l, vgs, vdd, vdd / points,
                             kind, section)
        ax.plot(vds, idd * 1e6, color=_C[i % len(_C)], lw=1.7,
                label=f'Vgs = {vgs:.2f} V')
        c = _device(models, model, w, l, kind, section)
        c.Vg.dc_value = vgs @ u_V
        c.Vd.dc_value = vdd @ u_V
        d = operating_point(c).devices['m1']
        if d.vdsat is not None and d.id is not None:
            vsat = abs(d.vdsat)
            boundary.append((vsat, float(np.interp(vsat, vds, idd)) * 1e6))

    if len(boundary) > 1:
        boundary.sort()
        ax.plot([b[0] for b in boundary], [b[1] for b in boundary],
                'k--', lw=1.1, alpha=0.65, label='Vds = Vdsat (model)')

    style(ax, title or f'{model}  W/L = {w * 1e6:g}u/{l * 1e6:g}u',
          'Vds  [V]', 'Id  [uA]', legend=True)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return {'file': str(out), 'vgs_list': vgs_list, 'saturation_points': boundary}


def transfer(models, model='nch', w=10e-6, l=0.18e-6, vds=None, vdd=1.8,
             points=200, kind='nmos', section=None, out='transfer.png',
             title=None, log=True):
    """Id and gm against Vgs, with Vth marked. Id on a log axis shows the
    subthreshold slope, which is where the weak-inversion behaviour lives."""
    vds = vdd if vds is None else vds
    vgs = np.linspace(0, vdd, points)
    idd, gm = [], []
    for v in vgs:
        c = _device(models, model, w, l, kind, section)
        c.Vg.dc_value = float(v) @ u_V
        c.Vd.dc_value = vds @ u_V
        d = operating_point(c).devices['m1']
        idd.append(abs(d.id) if d.id is not None else np.nan)
        gm.append(abs(d.gm) if d.gm is not None else np.nan)
    idd, gm = np.array(idd), np.array(gm)

    c = _device(models, model, w, l, kind, section)
    c.Vg.dc_value = (0.7 * vdd) @ u_V
    c.Vd.dc_value = vds @ u_V
    vth = operating_point(c).devices['m1'].vth

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.2), dpi=140)
    a1.plot(vgs, idd * 1e6, color=_C[0], lw=1.8)
    if log:
        a1.set_yscale('log')
        a1.set_ylim(max(idd.min() * 1e6, 1e-6), idd.max() * 1e6 * 2)
    if vth:
        for ax in (a1, a2):
            ax.axvline(abs(vth), color='k', ls=':', lw=1.1, alpha=0.7)
        a1.annotate(f'Vth = {abs(vth):.3f} V', xy=(abs(vth), idd.max() * 1e6 * 0.2),
                    xytext=(6, 0), textcoords='offset points', fontsize=8)
    style(a1, 'Id vs Vgs' + (' (log)' if log else ''), 'Vgs  [V]', 'Id  [uA]')
    a2.plot(vgs, gm * 1e6, color=_C[1], lw=1.8)
    style(a2, 'gm vs Vgs', 'Vgs  [V]', 'gm  [uS]')
    fig.suptitle(title or f'{model}  W/L = {w * 1e6:g}u/{l * 1e6:g}u  at Vds = {vds:.2f} V',
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return {'file': str(out), 'vth': vth, 'id_max': float(idd.max()),
            'gm_max': float(gm.max())}


def gm_id_chart(models, model='nch', w=10e-6, lengths=None, vdd=1.8,
                points=60, kind='nmos', section=None, out='gm_id.png',
                title=None):
    """The gm/ID design chart, for several channel lengths.

    Why this chart. Sizing by picking a W and iterating is slow and gives no
    insight. gm/ID is nearly independent of W, so you choose an operating point
    from these curves - high gm/ID for efficiency and gain, low for speed - read
    off ID/W, and the width follows directly from the current you need.
    """
    lengths = lengths or [0.18e-6, 0.5e-6, 1e-6]
    vgs = np.linspace(0.15 * vdd, vdd, points)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), dpi=140)
    table = {}

    for i, l in enumerate(lengths):
        gm_id, id_w, gain, vov = [], [], [], []
        for v in vgs:
            c = _device(models, model, w, l, kind, section)
            c.Vg.dc_value = float(v) @ u_V
            c.Vd.dc_value = (vdd / 2) @ u_V
            d = operating_point(c).devices['m1']
            if d.id is None or d.gm is None or abs(d.id) < 1e-14:
                gm_id.append(np.nan); id_w.append(np.nan)
                gain.append(np.nan); vov.append(np.nan)
                continue
            gm_id.append(abs(d.gm) / abs(d.id))
            id_w.append(abs(d.id) / w)
            gain.append(d.intrinsic_gain if d.intrinsic_gain else np.nan)
            vov.append(d.vov if d.vov is not None else np.nan)
        colour = _C[i % len(_C)]
        label = f'L = {l * 1e6:g} um'
        axes[0].plot(vov, gm_id, color=colour, lw=1.8, label=label)
        axes[1].plot(gm_id, np.array(id_w), color=colour, lw=1.8, label=label)
        axes[2].plot(gm_id, gain, color=colour, lw=1.8, label=label)
        table[f'{l * 1e6:g}um'] = {'gm_id': gm_id, 'id_w': id_w,
                                   'gain': gain, 'vov': vov}

    style(axes[0], 'gm/ID vs overdrive', 'Vov = Vgs - Vth  [V]', 'gm/ID  [1/V]',
          legend=True)
    axes[1].set_yscale('log')
    style(axes[1], 'current density', 'gm/ID  [1/V]', 'ID/W  [A/m]', legend=True)
    style(axes[2], 'intrinsic gain', 'gm/ID  [1/V]', 'gm*ro', legend=True)
    fig.suptitle(title or f'gm/ID design chart  -  {model}', fontsize=11)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return {'file': str(out), 'lengths': list(lengths), 'data': table}


def bode(freq, transfer_fn, out='bode.png', title=None, mark=True):
    """Magnitude and phase, with the -3 dB point, unity gain and phase margin.

    Pass the complex array from ``ac()``; the annotations come from
    ``bode_summary`` so the plot and the reported numbers cannot disagree.
    """
    freq = np.asarray(freq, dtype=float)
    h = np.asarray(transfer_fn, dtype=complex)
    s = bode_summary(freq, h)

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.4, 5.6), dpi=140, sharex=True)
    a1.semilogx(freq, db20(h), color=_C[0], lw=1.8)
    a2.semilogx(freq, unwrap_deg(h), color=_C[1], lw=1.8)

    if mark:
        if s['f_3db']:
            a1.axvline(s['f_3db'], color='k', ls=':', lw=1.1, alpha=0.7)
            a1.annotate(f'f-3dB = {_eng(s["f_3db"])}Hz',
                        xy=(s['f_3db'], db20(h)[0] - 3), xytext=(6, 4),
                        textcoords='offset points', fontsize=8)
        if s['ugb']:
            for ax in (a1, a2):
                ax.axvline(s['ugb'], color='#c05621', ls='--', lw=1.1, alpha=0.8)
            a1.annotate(f'UGB = {_eng(s["ugb"])}Hz', xy=(s['ugb'], 0),
                        xytext=(6, 6), textcoords='offset points', fontsize=8)
        if s.get('phase_margin') is not None:
            a2.annotate(f'PM = {s["phase_margin"]:.1f} deg',
                        xy=(s['ugb'], s['phase_at_ugb']), xytext=(6, 8),
                        textcoords='offset points', fontsize=8)
        a1.axhline(0, color='grey', lw=0.7, alpha=0.5)

    style(a1, title or 'Frequency response', None, 'magnitude  [dB]')
    style(a2, None, 'frequency  [Hz]', 'phase  [deg]')
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return {'file': str(out), **{k: v for k, v in s.items() if k != 'dc_gain_db'}}


def transient(time, signals, out='transient.png', title=None, ylabel='V'):
    """Time-domain waveforms. ``signals`` is {label: array}."""
    time = np.asarray(time, dtype=float)
    fig, ax = plt.subplots(figsize=(7.4, 4.2), dpi=140)
    for i, (label, values) in enumerate(signals.items()):
        ax.plot(time * 1e9, np.asarray(values, dtype=float),
                color=_C[i % len(_C)], lw=1.6, label=label)
    style(ax, title or 'Transient response', 'time  [ns]', ylabel,
          legend=len(signals) > 1)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return {'file': str(out)}


def sweep_metric(build, values, measure, xlabel='design variable',
                 ylabel='metric', out='sweep.png', title=None, target=None,
                 logx=False, refine=True, tol=1e-4, max_refine=40):
    """Sweep any design variable and plot any measured quantity.

    ``build(value)`` returns a Circuit, ``measure(op)`` returns a number from its
    operating point. Draw a ``target`` line and the crossing is the value to use,
    which is usually the question the sweep is being run to answer.

    The crossing is refined by bisection rather than read off the polyline.
    Interpolating linearly between sweep points is only as good as the sweep is
    dense, and these curves are steep: on a width sweep, straight interpolation
    missed a 0.9 V target by more than 100 mV. ``refine=False`` returns the plain
    interpolated value. ``target_crossing_error`` reports what was actually
    achieved, so the number can be trusted or discarded on evidence.
    """
    xs, ys = [], []
    for v in values:
        try:
            ys.append(float(measure(operating_point(build(v)))))
            xs.append(float(v))
        except Exception:
            continue
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=140)
    ax.plot(xs, ys, color=_C[0], lw=1.8, marker='o', ms=3)
    hit = None
    hit_error = None
    if target is not None:
        ax.axhline(target, color='#c05621', ls='--', lw=1.2,
                   label=f'target = {target:g}')
        arr = np.array(ys)
        sign = np.sign(arr - target)
        for i in range(len(arr) - 1):
            if sign[i] != sign[i + 1] and sign[i] != 0:
                t = (target - arr[i]) / (arr[i + 1] - arr[i])
                hit = xs[i] + t * (xs[i + 1] - xs[i])
                if refine:
                    lo, hi = xs[i], xs[i + 1]
                    f_lo = arr[i] - target
                    for _ in range(max_refine):
                        mid = 0.5 * (lo + hi)
                        try:
                            f_mid = float(measure(operating_point(build(mid)))) - target
                        except Exception:
                            break
                        if abs(f_mid) <= tol * max(abs(target), 1e-12):
                            hit, f_lo = mid, f_mid
                            break
                        if f_mid * f_lo > 0:
                            lo, f_lo = mid, f_mid
                        else:
                            hi = mid
                        hit = mid
                    hit_error = f_lo
                ax.axvline(hit, color='k', ls=':', lw=1.1)
                ax.annotate(f'{hit:.4g}', xy=(hit, target), xytext=(6, 6),
                            textcoords='offset points', fontsize=8)
                break
        ax.legend(fontsize=8, frameon=False)
    if logx:
        ax.set_xscale('log')
    style(ax, title or f'{ylabel} vs {xlabel}', xlabel, ylabel)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return {'file': str(out), 'x': xs, 'y': ys, 'target_crossing': hit,
            'target_crossing_error': hit_error}


def _eng(value):
    """Engineering notation for an axis annotation."""
    if not value:
        return '0'
    for scale, suffix in ((1e9, 'G'), (1e6, 'M'), (1e3, 'k'), (1, ''),
                          (1e-3, 'm'), (1e-6, 'u'), (1e-9, 'n')):
        if abs(value) >= scale:
            return f'{value / scale:.3g} {suffix}'
    return f'{value:.3g} '
