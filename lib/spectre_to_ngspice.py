"""Spectre IR -> ngspice deck, plus simulate/tune helpers.

Cadence Spectre is not installed here. This module translates the parser IR
into an ngspice deck and (when a real model is supplied) runs it through the
already-patched analog_spice ngspice shared library.

Honest about gaps: constructs with no ngspice equivalent go in `unsupported`
and are not silently rewritten into a different analysis.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
import sys
from pathlib import Path
from typing import Iterable


LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from spectre_netlist import (  # noqa: E402
    Analysis,
    Include,
    Instance,
    Model,
    Netlist,
    Subckt,
    find_instances,
    set_instance_param,
    set_param,
    spectre_number,
)


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------

@dataclass
class TranslationResult:
    netlist: str
    warnings: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    analyses: list = field(default_factory=list)
    node_map: dict = field(default_factory=dict)


@dataclass
class TuneResult:
    """Outcome of tune(). If a target cannot be met, met is False and closest
    holds the best achieved values. Do not read met=True unless every target
    is inside its tol.
    """
    knobs: dict[str, float]
    achieved: dict[str, float]
    iterations: int
    met: bool
    closest: dict[str, float]
    targets_met: dict[str, bool]
    messages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NODE_BAD = re.compile(r"[<>\[\]!@#]")
_LINT_RE = re.compile(r"\d+[AaFf]\b")
_NUM_TOKEN = re.compile(
    r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]+)?$"
)

_MOS_MASTERS = frozenset({"nmos", "nch", "pmos", "pch"})
_MOS_MODEL_TYPES = frozenset({"nmos", "pmos", "nch", "pch"})

_MODEL_LEVEL = {
    "bsim4": ("54", "nmos"),
    "bsim3v3": ("49", "nmos"),
    "bsim3": ("49", "nmos"),
    "mos1": ("1", "nmos"),
}

_KEEP_INST = {
    "resistor": frozenset({"r"}),
    "capacitor": frozenset({"c"}),
    "inductor": frozenset({"l"}),
    "vsource": frozenset({"dc", "mag", "type"}),
    "isource": frozenset({"dc"}),
    "mos": frozenset({"w", "l", "m"}),
}

_SUPPORTED_ANALYSIS = frozenset({"dc", "ac", "tran", "noise", "op"})
_UNSUPPORTED_ANALYSIS = frozenset({
    "stb", "pss", "pz", "xf", "sp", "montecarlo", "envlp",
    "sweep", "alter", "altergroup", "tf", "tdr", "pac", "pnoise",
    "pxf", "pstb", "psp", "qpss", "qpac", "qpstb", "qpnoise", "qpxf",
    "dcmatch", "reliability", "hb", "hbac", "hbnoise", "sens",
})

_NG_OPTIONS = frozenset({
    "reltol", "gmin", "tnom", "abstol", "vntol", "chgtol", "trtol",
    "method", "maxord", "itl1", "itl2", "itl4",
})

_VIRTUOSO_FUN = re.compile(r"(pPar|VAR|iPar|ppar|var|ipar)\s*\(")

# analog_spice lives beside this module in lib/, so locate it relatively
# rather than by absolute path.
_ANALOG_SPICE_LIB = str(Path(__file__).resolve().parent)

_EXTRA_SUFFIX = {
    "g": 1e9,
    "t": 1e12,
    "x": 1e6,
    "meg": 1e6,
}


# ---------------------------------------------------------------------------
# Number / node helpers
# ---------------------------------------------------------------------------

def _fmt_float(n: float) -> str:
    """Plain unsuffixed float. Never emit a trailing A/F scale trap."""
    if n == 0:
        return "0"
    if n == int(n) and abs(n) < 1e15:
        as_int = int(n)
        if as_int == n:
            return str(as_int)
    s = format(n, ".12g")
    if s and s[-1] in "AaFf":
        s = format(n, ".12e")
    return s


def _strip_wrap(text: str) -> str:
    s = str(text).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    if s.startswith("{") and s.endswith("}") and len(s) >= 2:
        s = s[1:-1].strip()
    return s


def parse_number(text) -> tuple[float | None, str | None]:
    """Return (value, note). value is None for a true expression.

    Always tries spectre_number() first (Spectre M=mega, m=milli). A few
    extra recoveries exist only so we do not emit lint-failing unit tails
    such as 100uA; those recoveries are reported in the note.
    """
    if text is None:
        return None, None
    raw = str(text).strip()
    if not raw:
        return None, None
    n = spectre_number(raw)
    if n is not None:
        return n, None
    inner = _strip_wrap(raw)
    if inner != raw:
        n = spectre_number(inner)
        if n is not None:
            return n, None
    for unit in ("Ohm", "ohm", "Hz", "V", "W", "A", "F", "s"):
        if inner.endswith(unit) and len(inner) > len(unit):
            n = spectre_number(inner[: -len(unit)])
            if n is not None:
                return n, "stripped unit %s from %s" % (unit, raw)
    m = _NUM_TOKEN.match(inner)
    if m and m.group(2):
        suf = m.group(2)
        key = suf.lower()
        if key in _EXTRA_SUFFIX:
            return float(m.group(1)) * _EXTRA_SUFFIX[key], (
                "spectre_number(%r) was None; used extra suffix %s" % (raw, suf)
            )
    return None, None


def emit_value(text, warnings: list[str] | None = None, where: str = "") -> str:
    """Literal -> unsuffixed float; expression -> {expr} plus a warning."""
    n, note = parse_number(text)
    if note and warnings is not None:
        warnings.append("%s: %s" % (where or "value", note))
    if n is not None:
        return _fmt_float(n)
    inner = _strip_wrap(text)
    if warnings is not None:
        warnings.append(
            "%s: expression %s emitted in { } (not a Spectre numeric literal)"
            % (where or "value", text)
        )
    return "{%s}" % inner


def sanitize_node(name: str, node_map: dict[str, str]) -> str:
    raw = str(name)
    if raw == "0":
        node_map.setdefault("0", "0")
        return "0"
    mapped = _NODE_BAD.sub("_", raw)
    node_map[raw] = mapped
    return mapped


def lint(deck: str) -> list[str]:
    """Regression guard: tokens ngspice would read as atto (A) or femto (F)."""
    offenders: list[str] = []
    for line in str(deck).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue
        for m in _LINT_RE.finditer(line):
            offenders.append("%s  (token %s)" % (stripped, m.group(0)))
    return offenders


def _spice_path(path) -> str:
    return str(path).replace("\\", "/")


def _is_mos_model_type(mtype: str) -> bool:
    t = (mtype or "").lower()
    return t in _MOS_MODEL_TYPES or t in _MODEL_LEVEL


def _mos_device_type(mtype: str, params: dict) -> str:
    typ = str(params.get("type", "")).strip().lower()
    if typ in ("n", "nmos", "nch"):
        return "nmos"
    if typ in ("p", "pmos", "pch"):
        return "pmos"
    mt = (mtype or "").lower()
    if mt in ("pmos", "pch"):
        return "pmos"
    if mt in ("nmos", "nch"):
        return "nmos"
    return "nmos"


# ---------------------------------------------------------------------------
# Translator
# ---------------------------------------------------------------------------

class _Translator:
    def __init__(self, nl: Netlist, models=None, top=None):
        self.nl = nl
        self.models_arg = models
        self.top = top
        self.warnings: list[str] = []
        self.unsupported: list[str] = []
        self.analyses_out: list = []
        self.node_map: dict[str, str] = {}
        self.subckt_names: set[str] = set()
        self.mos_models: set[str] = set(m.lower() for m in _MOS_MASTERS)
        self.known_models: dict[str, str] = {}
        self.top_inst: dict[str, str] = {}
        self.lines: list[str] = []

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def unsup(self, msg: str) -> None:
        self.unsupported.append(msg)

    def emit(self, line: str) -> None:
        self.lines.append(line)

    def collect(self) -> None:
        def walk(stmts, subckts):
            for sk in (subckts or {}).values():
                if sk is None:
                    continue
                self.subckt_names.add(sk.name)
                walk(getattr(sk, "statements", None) or [], None)
            for st in stmts or []:
                if isinstance(st, Subckt):
                    self.subckt_names.add(st.name)
                    walk(st.statements, None)
                elif isinstance(st, Model):
                    self._register_model(st)

        walk(self.nl.statements, getattr(self.nl, "subckts", None))
        extra = self._normalize_models_arg()
        for name, kind in extra.get("model_types", {}).items():
            self.mos_models.add(name.lower())
            self.known_models[name] = kind

    def _register_model(self, mdl: Model) -> None:
        mtype = (mdl.mtype or "").lower()
        if mtype in _MODEL_LEVEL or mtype in _MOS_MODEL_TYPES:
            self.mos_models.add(mdl.name.lower())
            self.known_models[mdl.name] = mdl.mtype
        elif mtype in ("nmos", "pmos"):
            self.mos_models.add(mdl.name.lower())
            self.known_models[mdl.name] = mtype

    def _normalize_models_arg(self) -> dict:
        """Accept a path, list of paths, or {model_name: nmos|pmos}."""
        out = {"paths": [], "model_types": {}}
        arg = self.models_arg
        if arg is None:
            return out
        if isinstance(arg, dict):
            for k, v in arg.items():
                out["model_types"][str(k)] = str(v)
            return out
        if isinstance(arg, (list, tuple)):
            items = list(arg)
        else:
            items = [arg]
        for item in items:
            if isinstance(item, dict):
                for k, v in item.items():
                    out["model_types"][str(k)] = str(v)
            else:
                out["paths"].append(item)
        return out

    def classify_master(self, master: str) -> str:
        m = master or ""
        low = m.lower()
        if m in self.subckt_names:
            return "subckt"
        if low in self.subckt_names:
            return "subckt"
        if low in ("resistor", "res"):
            return "resistor"
        if low in ("capacitor", "cap"):
            return "capacitor"
        if low in ("inductor", "ind"):
            return "inductor"
        if low in ("vsource", "vsrc"):
            return "vsource"
        if low in ("isource", "isrc"):
            return "isource"
        if low == "iprobe":
            return "iprobe"
        if low in ("port",):
            return "port"
        if low in self.mos_models or low in _MOS_MASTERS:
            return "mos"
        if low in ("nmos", "pmos"):
            return "mos"
        return "unknown"

    def spice_inst_name(self, prefix: str, name: str) -> str:
        return prefix + name

    def map_node(self, name: str) -> str:
        return sanitize_node(name, self.node_map)

    def map_nodes(self, nodes: Iterable[str]) -> list[str]:
        return [self.map_node(n) for n in nodes]

    def translate(self) -> TranslationResult:
        self.collect()
        title = (self.nl.title or "translated spectre netlist").strip()
        if not title:
            title = "translated spectre netlist"
        # First line is the ngspice title (must not be a card).
        self.emit(title.replace("\n", " "))
        self.emit("* translated by spectre_to_ngspice; numbers unsuffixed (Spectre M=1e6)")

        if self.top:
            if self.top not in self.subckt_names:
                self.warn("top=%r was not found among subckt names %s" % (
                    self.top, sorted(self.subckt_names)))
            else:
                self.emit("* top cell: %s" % self.top)

        extra = self._normalize_models_arg()
        for path in extra["paths"]:
            p = _spice_path(path)
            self.emit(".include \"%s\"" % p)
            self.warn("added models include %s" % p)

        self._emit_includes(self.nl.includes)
        self._emit_params(self.nl.parameters, global_=True)
        self._emit_options()

        for st in self.nl.statements:
            if isinstance(st, Model):
                self._emit_model(st)
            elif isinstance(st, Subckt):
                self._emit_subckt(st)

        # Nested-only subckts already emitted inside parents. Subckts that
        # appear only in nl.subckts (parser lift) and not as a top statement
        # still need a definition if we did not emit them via a parent.
        emitted = set()
        for st in self.nl.statements:
            if isinstance(st, Subckt):
                emitted.add(st.name)
                emitted.update(self._nested_names(st))
        for name, sk in (self.nl.subckts or {}).items():
            if name not in emitted:
                self._emit_subckt(sk)
                emitted.add(name)

        for st in self.nl.statements:
            if isinstance(st, Instance):
                self._emit_instance(st, top=True)

        self._emit_saves()
        self._emit_analyses()
        self.emit(".end")
        deck = "\n".join(self.lines) + "\n"
        offenders = lint(deck)
        if offenders:
            self.warn("lint() found atto/femto trap tokens after emit: %s" % offenders)
        return TranslationResult(
            netlist=deck,
            warnings=self.warnings,
            unsupported=self.unsupported,
            analyses=self.analyses_out,
            node_map=dict(self.node_map),
        )

    def _nested_names(self, sk: Subckt) -> set[str]:
        out = {sk.name}
        for st in sk.statements or []:
            if isinstance(st, Subckt):
                out |= self._nested_names(st)
        return out

    def _emit_includes(self, includes: list) -> None:
        for inc in includes or []:
            kind = getattr(inc, "kind", "include") or "include"
            path = getattr(inc, "path", "") or ""
            section = getattr(inc, "section", None)
            if kind == "ahdl_include":
                self.unsup(
                    "ahdl_include %s: Verilog-A is not translated (ngspice has no ADE Verilog-A path here)"
                    % path
                )
                continue
            if not path:
                self.warn("empty include path dropped")
                continue
            missing = ("$" in path) or (not Path(path).exists())
            mapped = _spice_path(path)
            if missing:
                self.warn(
                    "include %s section=%s omitted: path has $ENV or file is not on this machine"
                    % (path, section)
                )
                continue
            if section or kind == "lib":
                self.emit(".lib \"%s\" %s" % (mapped, section or "tt"))
            else:
                self.emit(".include \"%s\"" % mapped)

    def _emit_params(self, params: dict, global_=False, prefix="") -> list[str]:
        bits = []
        for name, val in (params or {}).items():
            ev = emit_value(val, self.warnings, where="param %s" % name)
            if _VIRTUOSO_FUN.search(str(val)):
                self.unsup(
                    "parameter %s=%s: Virtuoso pPar/VAR/iPar is not an ngspice expression"
                    % (name, val)
                )
            if global_:
                self.emit(".param %s=%s" % (name, ev))
            else:
                bits.append("%s=%s" % (name, ev))
        return bits

    def _emit_options(self) -> None:
        opts = getattr(self.nl, "options", None) or {}
        keep = []
        for k, v in opts.items():
            lk = k.lower()
            if lk == "temp":
                ev = emit_value(v, self.warnings, where="option temp")
                self.emit(".temp %s" % ev)
                continue
            if lk == "save":
                continue
            if lk in _NG_OPTIONS:
                ev = emit_value(v, self.warnings, where="option %s" % k)
                keep.append("%s=%s" % (lk, ev))
            else:
                self.warn("option %s=%s dropped (no ngspice mapping)" % (k, v))
        if keep:
            self.emit(".options %s" % " ".join(keep))

    def _emit_model(self, mdl: Model) -> None:
        mtype = (mdl.mtype or "").lower()
        params = dict(mdl.params or {})
        if mtype in _MODEL_LEVEL:
            level, _default_dev = _MODEL_LEVEL[mtype]
            dev = _mos_device_type(mtype, params)
            params.pop("type", None)
            if "level" not in {k.lower() for k in params}:
                params = dict(level=level, **params)
            self._write_model_card(mdl.name, dev, params)
            return
        if mtype in ("nmos", "pmos"):
            params.pop("type", None)
            self._write_model_card(mdl.name, mtype, params)
            return
        self.unsup(
            "model %s %s: no ngspice level mapping (only bsim4->54, bsim3v3->49, mos1->1, nmos, pmos)"
            % (mdl.name, mdl.mtype)
        )

    def _write_model_card(self, name: str, dev: str, params: dict) -> None:
        bits = []
        for k, v in params.items():
            ev = emit_value(v, self.warnings, where="model %s %s" % (name, k))
            bits.append("%s=%s" % (k, ev))
        body = (" " + " ".join(bits)) if bits else ""
        self.emit(".model %s %s%s" % (name, dev, body))

    def _emit_subckt(self, sk: Subckt) -> None:
        ports = " ".join(self.map_nodes(sk.ports or []))
        param_bits = self._emit_params(sk.params or {}, global_=False)
        header = ".subckt %s %s" % (sk.name, ports)
        if param_bits:
            header += " PARAMS: %s" % " ".join(param_bits)
        self.emit(header.strip())
        for st in sk.statements or []:
            if isinstance(st, Subckt):
                self._emit_subckt(st)
            elif isinstance(st, Model):
                self._emit_model(st)
            elif isinstance(st, Instance):
                self._emit_instance(st, top=False)
            elif isinstance(st, Include):
                self._emit_includes([st])
            elif isinstance(st, Analysis):
                self.warn(
                    "analysis %s %s inside subckt %s ignored at this scope"
                    % (st.name, st.type, sk.name)
                )
        self.emit(".ends %s" % sk.name)

    def _repair_spice_source(self, inst: Instance) -> tuple[list[str], str, dict]:
        """Parser leaves `VIN vin 0 DC 0.7` as nodes=[vin,0,DC] master=0.7."""
        nodes = list(inst.nodes or [])
        master = inst.master
        params = dict(inst.params or {})
        flags = {n.upper() for n in nodes}
        n_master, _ = parse_number(master)
        if n_master is not None and (flags & {"DC", "AC"}):
            real_nodes = [n for n in nodes if n.upper() not in {"DC", "AC"}]
            params.setdefault("dc", master)
            letter = ""
            for ch in inst.name:
                if ch.isalpha():
                    letter = ch.upper()
                    break
            kind = "vsource" if letter == "V" else "isource" if letter == "I" else "vsource"
            self.warn(
                "repaired spice-style source %s (parser stored master=%r nodes=%s)"
                % (inst.name, master, inst.nodes)
            )
            return real_nodes, kind, params
        return nodes, master, params

    def _emit_instance(self, inst: Instance, top: bool) -> None:
        nodes, master, params = self._repair_spice_source(inst)
        kind = self.classify_master(master)
        if kind == "unknown":
            # w/l without a known model: still a MOSFET card, but flag it.
            if "w" in params or "l" in params:
                kind = "mos"
                self.warn(
                    "instance %s master %s is not a known model/subckt; emitted as MOS"
                    % (inst.name, master)
                )
                self.unsup(
                    "instance %s master %s: model/subckt was never defined"
                    % (inst.name, master)
                )
            elif master in self.subckt_names:
                kind = "subckt"
            else:
                kind = "subckt"
                self.warn(
                    "instance %s master %s treated as subckt call (unknown primitive)"
                    % (inst.name, master)
                )
                self.unsup(
                    "instance %s master %s: no primitive mapping"
                    % (inst.name, master)
                )

        mapped_nodes = self.map_nodes(nodes)
        if kind == "subckt":
            spice = self.spice_inst_name("X", inst.name)
            if top:
                self.top_inst[inst.name] = spice
            extra = []
            for k, v in params.items():
                extra.append("%s=%s" % (k, emit_value(v, self.warnings, where="%s.%s" % (inst.name, k))))
            tail = (" PARAMS: " + " ".join(extra)) if extra else ""
            self.emit("%s %s %s%s" % (spice, " ".join(mapped_nodes), master, tail))
            return

        if kind == "mos":
            spice = self.spice_inst_name("M", inst.name)
            if top:
                self.top_inst[inst.name] = spice
            if len(mapped_nodes) < 4:
                self.warn(
                    "MOS %s has %d nodes; padding bulk/source with 0"
                    % (inst.name, len(mapped_nodes))
                )
                while len(mapped_nodes) < 4:
                    mapped_nodes.append("0")
            if len(mapped_nodes) > 4:
                self.warn("MOS %s has extra nodes %s; using first four" % (
                    inst.name, mapped_nodes[4:]))
                mapped_nodes = mapped_nodes[:4]
            bits = self._kept_params(inst.name, params, _KEEP_INST["mos"])
            self.emit("%s %s %s %s" % (
                spice, " ".join(mapped_nodes), master, " ".join(bits)
            ))
            return

        if kind in ("resistor", "capacitor", "inductor"):
            prefix = {"resistor": "R", "capacitor": "C", "inductor": "L"}[kind]
            spice = self.spice_inst_name(prefix, inst.name)
            if top:
                self.top_inst[inst.name] = spice
            if len(mapped_nodes) < 2:
                self.warn("%s %s needs 2 nodes, got %s" % (kind, inst.name, mapped_nodes))
                while len(mapped_nodes) < 2:
                    mapped_nodes.append("0")
            key = {"resistor": "r", "capacitor": "c", "inductor": "l"}[kind]
            val = params.get(key)
            if val is None:
                self.warn("%s %s missing %s; using 0" % (kind, inst.name, key))
                ev = "0"
            else:
                ev = emit_value(val, self.warnings, where="%s.%s" % (inst.name, key))
            dropped = [k for k in params if k != key]
            for k in dropped:
                self.warn("instance %s: dropped param %s=%s" % (inst.name, k, params[k]))
                if _VIRTUOSO_FUN.search(str(params[k])):
                    self.unsup(
                        "instance %s param %s=%s: Virtuoso pPar/VAR/iPar"
                        % (inst.name, k, params[k])
                    )
            self.emit("%s %s %s" % (spice, " ".join(mapped_nodes[:2]), ev))
            return

        if kind in ("vsource", "isource"):
            prefix = "V" if kind == "vsource" else "I"
            spice = self.spice_inst_name(prefix, inst.name)
            if top:
                self.top_inst[inst.name] = spice
            if len(mapped_nodes) < 2:
                while len(mapped_nodes) < 2:
                    mapped_nodes.append("0")
            dc = params.get("dc", "0")
            mag = params.get("mag")
            typ = params.get("type")
            bits = ["%s %s" % (spice, " ".join(mapped_nodes[:2]))]
            bits.append("DC")
            bits.append(emit_value(dc, self.warnings, where="%s.dc" % inst.name))
            if mag is not None and kind == "vsource":
                bits.append("AC")
                bits.append(emit_value(mag, self.warnings, where="%s.mag" % inst.name))
            if typ is not None and str(typ).strip().lower() not in ("dc", "dc"):
                self.warn(
                    "vsource/isource %s type=%s: only DC/AC mag emitted; waveform not translated"
                    % (inst.name, typ)
                )
                if str(typ).strip().lower() not in ("ac",):
                    self.unsup(
                        "source %s type=%s: ngspice PWL/sine/pulse card not emitted"
                        % (inst.name, typ)
                    )
            for k, v in params.items():
                if k not in ("dc", "mag", "type"):
                    self.warn("instance %s: dropped param %s=%s" % (inst.name, k, v))
            self.emit(" ".join(bits))
            return

        if kind == "iprobe":
            spice = self.spice_inst_name("V", inst.name)
            if top:
                self.top_inst[inst.name] = spice
            if len(mapped_nodes) < 2:
                while len(mapped_nodes) < 2:
                    mapped_nodes.append("0")
            self.warn(
                "iprobe %s approximated as a 0 V voltage source (current probe)"
                % inst.name
            )
            self.emit("%s %s DC 0" % (spice, " ".join(mapped_nodes[:2])))
            return

        if kind == "port":
            self.unsup("instance %s port: RF port has no ngspice equivalent here" % inst.name)
            return

        self.unsup("instance %s master %s: unhandled" % (inst.name, master))

    def _kept_params(self, inst_name: str, params: dict, keep: frozenset) -> list[str]:
        bits = []
        for k, v in params.items():
            if k in keep:
                ev = emit_value(v, self.warnings, where="%s.%s" % (inst_name, k))
                if _VIRTUOSO_FUN.search(str(v)):
                    self.unsup(
                        "instance %s param %s=%s: Virtuoso pPar/VAR/iPar"
                        % (inst_name, k, v)
                    )
                bits.append("%s=%s" % (k, ev))
            else:
                self.warn("instance %s: dropped param %s=%s" % (inst_name, k, v))
                if _VIRTUOSO_FUN.search(str(v)):
                    self.unsup(
                        "instance %s param %s=%s: Virtuoso pPar/VAR/iPar"
                        % (inst_name, k, v)
                    )
        return bits

    def _emit_saves(self) -> None:
        saves = list(getattr(self.nl, "saves", None) or [])
        opts = getattr(self.nl, "options", None) or {}
        save_mode = str(opts.get("save", "")).lower()
        if save_mode in ("allpub", "all"):
            self.emit(".save all")
        for tok in saves:
            mapped = self._map_save_token(tok)
            self.emit(".save %s" % mapped)

    def _map_save_token(self, tok: str) -> str:
        raw = str(tok)
        for sep in (".", ":"):
            if sep in raw:
                left, right = raw.split(sep, 1)
                left_m = self.top_inst.get(left, left)
                left_m = _NODE_BAD.sub("_", left_m)
                right_m = self.map_node(right) if sep == "." else _NODE_BAD.sub("_", right)
                self.warn(
                    "save %s mapped to %s%s%s (Spectre colon/dot save is not identical in ngspice)"
                    % (raw, left_m, sep, right_m)
                )
                return "%s%s%s" % (left_m, sep, right_m)
        return self.map_node(raw)

    def _lookup_src(self, name: str) -> str:
        if not name:
            return name
        if name in self.top_inst:
            return self.top_inst[name]
        # case-insensitive
        for k, v in self.top_inst.items():
            if k.lower() == name.lower():
                return v
        return name

    def _emit_analyses(self) -> None:
        for ana in getattr(self.nl, "analyses", None) or []:
            atype = (ana.type or "").lower()
            if atype in _UNSUPPORTED_ANALYSIS or atype not in _SUPPORTED_ANALYSIS:
                if atype == "op":
                    self.emit(".op")
                    self.analyses_out.append(ana)
                    continue
                self.unsup(
                    "analysis %s type=%s: ngspice has no equivalent; not emitted"
                    % (ana.name, ana.type)
                )
                continue
            if atype == "dc":
                self._emit_dc(ana)
            elif atype == "ac":
                self._emit_ac(ana)
            elif atype == "tran":
                self._emit_tran(ana)
            elif atype == "noise":
                self._emit_noise(ana)
            elif atype == "op":
                self.emit(".op")
                self.analyses_out.append(ana)

    def _emit_dc(self, ana: Analysis) -> None:
        p = dict(ana.params or {})
        if "_args" in p and not any(k in p for k in ("start", "stop", "dev", "param")):
            args = str(p["_args"]).split()
            out = []
            if args:
                out.append(self._lookup_src(args[0]))
                for tok in args[1:]:
                    out.append(emit_value(tok, self.warnings, where="dc %s" % ana.name))
            self.emit(".dc %s" % " ".join(out))
            self.analyses_out.append(ana)
            return
        has_sweep = any(k in p for k in ("start", "stop", "dev", "src"))
        if not has_sweep:
            self.warn(
                "analysis %s dc has no sweep (ADE operating point); emitted .op "
                "because ngspice .dc requires a source sweep"
                % ana.name
            )
            self.emit(".op")
            self.analyses_out.append(ana)
            return
        src = p.get("dev") or p.get("src") or p.get("param")
        src_name = self._lookup_src(str(src)) if src else ""
        # When param=dc and dev=VIN, sweep the source.
        if str(p.get("param", "")).lower() == "dc" and p.get("dev"):
            src_name = self._lookup_src(str(p["dev"]))
        start = emit_value(p.get("start", "0"), self.warnings, where="%s.start" % ana.name)
        stop = emit_value(p.get("stop", "0"), self.warnings, where="%s.stop" % ana.name)
        step = p.get("step")
        if step is None:
            step_e = "0.01"
            self.warn("dc %s missing step; using 0.01" % ana.name)
        else:
            step_e = emit_value(step, self.warnings, where="%s.step" % ana.name)
        if not src_name:
            self.unsup("analysis %s dc sweep: no source name (dev=) to emit .dc" % ana.name)
            return
        self.emit(".dc %s %s %s %s" % (src_name, start, stop, step_e))
        self.analyses_out.append(ana)

    def _emit_ac(self, ana: Analysis) -> None:
        p = dict(ana.params or {})
        start = emit_value(p.get("start", "1"), self.warnings, where="%s.start" % ana.name)
        stop = emit_value(p.get("stop", "1"), self.warnings, where="%s.stop" % ana.name)
        if "lin" in p:
            n = emit_value(p.get("lin"), self.warnings, where="%s.lin" % ana.name)
            self.emit(".ac lin %s %s %s" % (n, start, stop))
        else:
            n = p.get("dec", p.get("points", "10"))
            n_e = emit_value(n, self.warnings, where="%s.dec" % ana.name)
            self.emit(".ac dec %s %s %s" % (n_e, start, stop))
        self.analyses_out.append(ana)

    def _emit_tran(self, ana: Analysis) -> None:
        p = dict(ana.params or {})
        stop = emit_value(p.get("stop", p.get("tstop", "1e-6")), self.warnings,
                          where="%s.stop" % ana.name)
        if "step" in p or "tstep" in p:
            step = emit_value(p.get("step", p.get("tstep")), self.warnings,
                              where="%s.step" % ana.name)
        else:
            n_stop, _ = parse_number(p.get("stop", p.get("tstop", "1e-6")))
            step = _fmt_float((n_stop or 1e-6) / 50.0)
            self.warn("tran %s missing step; using stop/50 = %s" % (ana.name, step))
        self.emit(".tran %s %s" % (step, stop))
        self.analyses_out.append(ana)

    def _emit_noise(self, ana: Analysis) -> None:
        p = dict(ana.params or {})
        start = emit_value(p.get("start", "1"), self.warnings, where="%s.start" % ana.name)
        stop = emit_value(p.get("stop", "1"), self.warnings, where="%s.stop" % ana.name)
        n = emit_value(p.get("dec", "10"), self.warnings, where="%s.dec" % ana.name)
        src = p.get("iprobe") or p.get("src") or p.get("source")
        if not src:
            self.unsup("analysis %s noise: no iprobe/src to emit .noise" % ana.name)
            return
        src_name = self._lookup_src(str(src))
        out_node = None
        oprobe = p.get("oprobes") or p.get("oprobe") or p.get("output")
        if oprobe:
            op = str(oprobe)
            if op in self.node_map or op in (self.nl.saves or []):
                out_node = self.map_node(op)
            elif op in self.top_inst or any(
                isinstance(st, Instance) and st.name == op
                for st in self.nl.statements
            ):
                # resistor / source used as oprobe: use its first non-supply node
                inst = None
                for st in self.nl.statements:
                    if isinstance(st, Instance) and st.name == op:
                        inst = st
                        break
                if inst and inst.nodes:
                    cand = [n for n in inst.nodes if n not in ("0", "vdd", "vss")]
                    out_node = self.map_node(cand[0] if cand else inst.nodes[0])
                    self.warn(
                        "noise %s oprobe=%s is an instance; using node %s"
                        % (ana.name, op, out_node)
                    )
                else:
                    out_node = self.map_node(op)
            else:
                out_node = self.map_node(op)
        if out_node is None:
            if self.nl.saves:
                out_node = self.map_node(self.nl.saves[0])
                self.warn("noise %s: no oprobe, using save %s" % (ana.name, out_node))
            else:
                self.unsup("analysis %s noise: no output node (oprobe/save)" % ana.name)
                return
        self.emit(".noise v(%s) %s dec %s %s %s" % (out_node, src_name, n, start, stop))
        self.analyses_out.append(ana)


def translate(nl, models=None, top=None) -> TranslationResult:
    """Translate a spectre_netlist.Netlist IR into an ngspice deck."""
    if not isinstance(nl, Netlist):
        # Duck-typed IR from another builder: wrap the attributes we need.
        raise TypeError("translate() expects a spectre_netlist.Netlist")
    return _Translator(nl, models=models, top=top).translate()


# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------

def _import_analog_spice():
    path = _ANALOG_SPICE_LIB
    if path not in sys.path:
        sys.path.insert(0, path)
    import analog_spice
    return analog_spice


_NG = None
_NG_HAS_CIRC = False


def _ngspice():
    global _NG
    analog_spice = _import_analog_spice()
    if _NG is None:
        _NG = analog_spice._PatchedNgSpiceShared.new_instance()
    return _NG


def _reset_ng(ng) -> None:
    global _NG_HAS_CIRC
    if _NG_HAS_CIRC:
        try:
            ng.destroy("all")
        except Exception:
            pass
        try:
            ng.remove_circuit()
        except Exception:
            pass
    try:
        ng.clear_output()
    except Exception:
        pass


def _vector_array(vec):
    import numpy as np
    data = getattr(vec, "_data", None)
    if data is None:
        try:
            data = vec
        except Exception:
            return None
    return np.asarray(data)


def _scalar(arr):
    if arr is None:
        return None
    flat = arr.ravel()
    if flat.size == 0:
        return None
    val = flat[0]
    try:
        return float(val.real)
    except Exception:
        return float(val)


def simulate(nl_or_deck, models=None, top=None, **kwargs) -> dict:
    """Run a translated deck (or a Netlist) in analog_spice's patched ngspice.

    Returns a dict with keys:
      ok, error, nodes, branches, plots, analyses, warnings, deck
    Node voltages from the first .op plot (if any) are in nodes as floats.
    """
    warnings = []
    deck = None
    tr = None
    if isinstance(nl_or_deck, Netlist):
        tr = translate(nl_or_deck, models=models, top=top)
        deck = tr.netlist
        warnings.extend(tr.warnings)
    else:
        deck = str(nl_or_deck)
        if models:
            inc_lines = []
            paths = models if isinstance(models, (list, tuple)) else [models]
            for p in paths:
                if isinstance(p, dict):
                    continue
                inc_lines.append(".include \"%s\"" % _spice_path(p))
            if inc_lines:
                parts = deck.splitlines()
                if parts:
                    deck = parts[0] + "\n" + "\n".join(inc_lines) + "\n" + "\n".join(parts[1:]) + "\n"

    offenders = lint(deck)
    if offenders:
        return {
            "ok": False,
            "error": "deck fails lint() (atto/femto trap): %s" % offenders,
            "nodes": {},
            "branches": {},
            "plots": {},
            "analyses": [] if tr is None else tr.analyses,
            "warnings": warnings,
            "deck": deck,
        }

    global _NG_HAS_CIRC
    ng = _ngspice()
    _reset_ng(ng)
    try:
        ng.load_circuit(deck)
        _NG_HAS_CIRC = True
        ng.run()
    except Exception as exc:
        stdout = getattr(ng, "stdout", "") or ""
        stderr = getattr(ng, "stderr", "") or ""
        return {
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "stdout": stdout,
            "stderr": stderr,
            "nodes": {},
            "branches": {},
            "plots": {},
            "analyses": [] if tr is None else tr.analyses,
            "warnings": warnings,
            "deck": deck,
        }

    plots = {}
    nodes = {}
    branches = {}
    try:
        plot_names = list(ng.plot_names or [])
    except Exception:
        plot_names = []
    for pname in plot_names:
        if pname == "const":
            continue
        try:
            plot = ng.plot(None, pname)
        except Exception as exc:
            warnings.append("could not read plot %s: %s" % (pname, exc))
            continue
        pdata = {}
        for key, vec in plot.items():
            arr = _vector_array(vec)
            pdata[str(key)] = arr
            low = str(key).lower()
            if pname.lower().startswith("op"):
                if "#branch" in low:
                    branches[str(key)] = _scalar(arr)
                    # also a stripped alias: vvdd#branch -> vvdd
                    alias = low.replace("#branch", "").replace("i(", "").replace(")", "")
                    branches[alias] = _scalar(arr)
                else:
                    nname = str(key)
                    if nname.lower().startswith("v(") and nname.endswith(")"):
                        nname = nname[2:-1]
                    nodes[nname] = _scalar(arr)
                    nodes[nname.lower()] = _scalar(arr)
        plots[pname] = pdata

    return {
        "ok": True,
        "error": None,
        "nodes": nodes,
        "branches": branches,
        "plots": plots,
        "plot_names": plot_names,
        "analyses": [] if tr is None else tr.analyses,
        "warnings": warnings,
        "deck": deck,
        "stdout": getattr(ng, "stdout", "") or "",
        "stderr": getattr(ng, "stderr", "") or "",
    }


# ---------------------------------------------------------------------------
# Tune / apply
# ---------------------------------------------------------------------------

def _knob_key(knob: dict) -> str:
    inst = knob.get("instance")
    param = knob.get("param")
    if inst:
        return "%s.%s" % (inst, param)
    return str(param)


def _format_tuned_value(value) -> str:
    try:
        return _fmt_float(float(value))
    except (TypeError, ValueError):
        return str(value)


def apply_tuned(nl: Netlist, tuned_knobs) -> None:
    """Write tuned values back into the Spectre IR (span-edit, one token)."""
    if isinstance(tuned_knobs, TuneResult):
        items = tuned_knobs.knobs
    elif isinstance(tuned_knobs, dict):
        items = tuned_knobs
    elif isinstance(tuned_knobs, (list, tuple)):
        items = {}
        for kn in tuned_knobs:
            if "value" not in kn:
                continue
            items[_knob_key(kn)] = kn["value"]
    else:
        raise TypeError("apply_tuned expects a dict, list of knobs, or TuneResult")

    for key, value in items.items():
        sval = _format_tuned_value(value)
        if "." in str(key):
            inst, param = str(key).split(".", 1)
            # Prefer instance.param when the instance exists; else global.
            try:
                find_instances(nl, name_re=r"^%s$" % re.escape(inst))
                exists = any(
                    i.name == inst for i in find_instances(nl)
                )
            except Exception:
                exists = False
            if exists:
                set_instance_param(nl, inst, param, sval)
                continue
        if key in (nl.parameters or {}):
            set_param(nl, str(key), sval)
        elif "." in str(key):
            inst, param = str(key).split(".", 1)
            set_instance_param(nl, inst, param, sval)
        else:
            # instance-less: global first, else first instance that has it
            if str(key) in (nl.parameters or {}):
                set_param(nl, str(key), sval)
            else:
                found = False
                for inst in find_instances(nl):
                    if key in (inst.params or {}):
                        set_instance_param(nl, inst.name, str(key), sval)
                        found = True
                        break
                if not found:
                    raise KeyError("tuned knob %r not found as param or instance.param" % (key,))


def _apply_knobs_copy(nl: Netlist, values: dict) -> Netlist:
    copy = deepcopy(nl)
    apply_tuned(copy, values)
    return copy


def _rel_err(achieved: float, goal: float) -> float:
    if goal == 0:
        return abs(achieved)
    return abs(achieved - goal) / abs(goal)


def _eval_targets(nl: Netlist, values: dict, targets: list, models) -> tuple[dict, dict]:
    copy = _apply_knobs_copy(nl, values)
    # Speed: operating-point only if every measure looks DC.
    res = simulate(copy, models=models)
    if not res.get("ok"):
        raise RuntimeError("simulate failed during tune: %s" % res.get("error"))
    achieved = {}
    for t in targets:
        name = t.get("name") or t["measure"].__name__
        achieved[name] = float(t["measure"](res))
    return achieved, res


def tune(nl, knobs, targets, max_iter=40, models=None, **kwargs) -> TuneResult:
    """Fit knobs so each target measure is within relative tol of goal.

    Single knob + single target uses bisection. Otherwise scipy least_squares
    on the relative-error vector. If a target cannot be met, met is False and
    closest reports the best point actually reached.
    """
    if not knobs:
        raise ValueError("tune() needs at least one knob")
    if not targets:
        raise ValueError("tune() needs at least one target")

    keys = [_knob_key(k) for k in knobs]
    lows = [float(k["low"]) for k in knobs]
    highs = [float(k["high"]) for k in knobs]
    messages = []

    def values_from_x(x) -> dict:
        return {keys[i]: float(x[i]) for i in range(len(keys))}

    best_x = [(lo + hi) / 2.0 for lo, hi in zip(lows, highs)]
    best_ach = {}
    best_score = float("inf")
    iterations = 0
    met = False
    targets_met = {t.get("name") or t["measure"].__name__: False for t in targets}

    def consider(x, achieved, iters):
        nonlocal best_x, best_ach, best_score, met, targets_met, iterations
        iterations = max(iterations, iters)
        tm = {}
        score = 0.0
        all_ok = True
        for t in targets:
            name = t.get("name") or t["measure"].__name__
            goal = float(t["goal"])
            tol = float(t.get("tol", 0.02))
            a = achieved[name]
            rel = _rel_err(a, goal)
            ok = rel <= tol
            tm[name] = ok
            score += rel * rel
            if not ok:
                all_ok = False
        if score < best_score:
            best_score = score
            best_x = list(x)
            best_ach = dict(achieved)
            targets_met = tm
            met = all_ok
        elif all_ok and not met:
            best_x = list(x)
            best_ach = dict(achieved)
            targets_met = tm
            met = True

    # Seed the midpoint so "closest" is never empty if later steps fail.
    try:
        mid = [(lo + hi) / 2.0 for lo, hi in zip(lows, highs)]
        ach, _ = _eval_targets(nl, values_from_x(mid), targets, models)
        consider(mid, ach, 1)
    except Exception as exc:
        messages.append("midpoint evaluate failed: %s" % exc)

    one_d = len(knobs) == 1 and len(targets) == 1
    if one_d:
        lo, hi = lows[0], highs[0]
        t0 = targets[0]
        name = t0.get("name") or t0["measure"].__name__
        goal = float(t0["goal"])
        tol = float(t0.get("tol", 0.02))

        def f(xscalar):
            ach, _ = _eval_targets(nl, {keys[0]: xscalar}, targets, models)
            return ach[name] - goal, ach

        try:
            err_lo, ach_lo = f(lo)
            consider([lo], ach_lo, iterations + 1)
            err_hi, ach_hi = f(hi)
            consider([hi], ach_hi, iterations + 1)
        except Exception as exc:
            messages.append("bracket evaluate failed: %s" % exc)
            return TuneResult(
                knobs=values_from_x(best_x),
                achieved=dict(best_ach),
                iterations=iterations,
                met=False,
                closest=dict(best_ach),
                targets_met=targets_met,
                messages=messages + ["bisection could not evaluate the bracket"],
            )

        if err_lo == 0:
            consider([lo], ach_lo, iterations)
        elif err_hi == 0:
            consider([hi], ach_hi, iterations)
        elif err_lo * err_hi > 0:
            messages.append(
                "bisection: %s does not cross goal %s on [%g, %g] "
                "(errors %g and %g). Closest point kept; target not claimed met."
                % (name, goal, lo, hi, err_lo, err_hi)
            )
            # Sample a few interior points for a better "closest".
            for i in range(1, min(8, max_iter)):
                x = lo + (hi - lo) * i / 8.0
                try:
                    _, ach = f(x)
                    consider([x], ach, iterations + 1)
                except Exception as exc:
                    messages.append("sample %s failed: %s" % (x, exc))
        else:
            a, b = lo, hi
            fa = err_lo
            for i in range(max_iter):
                mid = 0.5 * (a + b)
                try:
                    fm, ach = f(mid)
                except Exception as exc:
                    messages.append("bisection mid failed: %s" % exc)
                    break
                consider([mid], ach, i + 1)
                if _rel_err(ach[name], goal) <= tol:
                    break
                if fm == 0:
                    break
                if fa * fm > 0:
                    a, fa = mid, fm
                else:
                    b = mid
            else:
                messages.append("bisection hit max_iter=%d" % max_iter)
    else:
        import numpy as np
        from scipy.optimize import least_squares

        x0 = np.array([(lo + hi) / 2.0 for lo, hi in zip(lows, highs)], dtype=float)

        def fun(x):
            nonlocal iterations
            iterations += 1
            try:
                ach, _ = _eval_targets(nl, values_from_x(x), targets, models)
                consider(list(x), ach, iterations)
                err = []
                for t in targets:
                    name = t.get("name") or t["measure"].__name__
                    goal = float(t["goal"])
                    scale = abs(goal) if goal != 0 else 1.0
                    err.append((ach[name] - goal) / scale)
                return err
            except Exception:
                return [1e6] * len(targets)

        try:
            least_squares(
                fun,
                x0,
                bounds=(lows, highs),
                max_nfev=max_iter,
                xtol=1e-10,
                ftol=1e-10,
            )
        except Exception as exc:
            messages.append("least_squares failed: %s" % exc)

    final_knobs = values_from_x(best_x)
    if not best_ach:
        messages.append("no successful evaluation; cannot claim any target was met")
        return TuneResult(
            knobs=final_knobs,
            achieved={},
            iterations=iterations,
            met=False,
            closest={},
            targets_met=targets_met,
            messages=messages,
        )

    if not met:
        for t in targets:
            name = t.get("name") or t["measure"].__name__
            if not targets_met.get(name):
                messages.append(
                    "target %s not met: goal=%s achieved=%s (closest, not pretended)"
                    % (name, t.get("goal"), best_ach.get(name))
                )

    return TuneResult(
        knobs=final_knobs,
        achieved=dict(best_ach),
        iterations=iterations,
        met=met,
        closest=dict(best_ach),
        targets_met=dict(targets_met),
        messages=messages,
    )


__all__ = [
    "TranslationResult",
    "TuneResult",
    "translate",
    "simulate",
    "tune",
    "apply_tuned",
    "lint",
    "emit_value",
    "parse_number",
    "sanitize_node",
]
