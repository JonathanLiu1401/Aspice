"""Cadence Spectre / ADE netlist parser and writer.

stdlib only. Round-trip is identity on raw_lines; edits rewrite one physical
line. Nested braces and parens are scanned with a depth-tracking tokenizer,
not a single regex.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


# ---------------------------------------------------------------------------
# IR (fixed contract)
# ---------------------------------------------------------------------------

@dataclass
class Include:
    path: str
    section: str | None
    kind: str  # 'include' | 'ahdl_include' | 'lib'


@dataclass
class Model:
    name: str
    mtype: str
    params: dict[str, str]
    line_no: int
    raw: str


@dataclass
class Instance:
    name: str
    nodes: list[str]
    master: str
    params: dict[str, str]
    line_no: int
    raw: str


@dataclass
class Subckt:
    name: str
    ports: list[str]
    params: dict[str, str]
    instances: list[Instance]
    statements: list
    line_no: int


@dataclass
class Analysis:
    name: str
    type: str
    params: dict[str, str]
    line_no: int
    raw: str


@dataclass
class Netlist:
    title: str
    language: str
    statements: list
    subckts: dict[str, Subckt]
    parameters: dict[str, str]
    includes: list[Include]
    analyses: list[Analysis]
    options: dict[str, str]
    saves: list[str]
    raw_lines: list[str]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANALYSIS_TYPES = frozenset({
    "dc", "ac", "tran", "noise", "stb", "pss", "pz", "xf", "sp",
    "montecarlo", "tf", "tdr", "envlp", "pac", "pnoise", "pxf", "pstb",
    "psp", "qpss", "qpac", "qpstb", "qpnoise", "qpxf", "dcmatch",
    "reliability", "hb", "hbac", "hbnoise", "op", "alter", "altergroup",
    "sweep", "sens", "info",
})

WRAPPER_TYPES = frozenset({"sweep", "alter", "altergroup", "montecarlo"})

# SPICE letter-prefix primitives whose last positional token is a value, not a model.
_SPICE_PASSIVE = {
    "r": ("resistor", "r"),
    "c": ("capacitor", "c"),
    "l": ("inductor", "l"),
}


def _spice_letter(name: str) -> str:
    i = 0
    n = len(name)
    while i < n and name[i] in "._":
        i += 1
    if i < n and name[i].isalpha():
        return name[i].lower()
    return ""

_SCALE = {
    "T": 1e12,
    "G": 1e9,
    "M": 1e6,
    "K": 1e3,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
    "a": 1e-18,
    "c": 1e-2,
}

_NUM_RE = re.compile(
    r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(meg|MEG|Meg|[TGMKkmunpfa])?$"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_netlist(text: str) -> Netlist:
    return _Parser(text).parse()


def parse_file(path) -> Netlist:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return parse_netlist(fh.read())


def write_netlist(netlist: Netlist) -> str:
    return "".join(netlist.raw_lines)


def set_param(netlist: Netlist, name: str, value) -> None:
    value = str(value)
    if name not in netlist.parameters:
        raise KeyError("global parameter %r not found" % (name,))
    netlist.parameters[name] = value
    spans = getattr(netlist, "_param_spans", None) or {}
    span = spans.get(name)
    if span is None:
        raise KeyError("global parameter %r has no source span" % (name,))
    _apply_span(netlist.raw_lines, span, value)


def set_instance_param(netlist: Netlist, inst_name: str, param: str, value) -> None:
    value = str(value)
    found = False
    for inst in _all_instances(netlist):
        if inst.name != inst_name:
            continue
        found = True
        inst.params[param] = value
        spans = getattr(inst, "_param_spans", None) or {}
        if param in spans:
            _apply_span(netlist.raw_lines, spans[param], value)
        else:
            _append_instance_param(netlist, inst, param, value)
    if not found:
        raise KeyError("instance %r not found" % (inst_name,))


def find_instances(netlist: Netlist, master=None, name_re=None) -> list[Instance]:
    pat = re.compile(name_re) if name_re is not None else None
    out: list[Instance] = []
    for inst in _all_instances(netlist):
        if master is not None and inst.master != master:
            continue
        if pat is not None and pat.search(inst.name) is None:
            continue
        out.append(inst)
    return out


def spectre_number(text: str) -> float | None:
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    m = _NUM_RE.match(s)
    if m is None:
        return None
    base = float(m.group(1))
    suf = m.group(2)
    if not suf:
        return base
    if suf.lower() == "meg":
        return base * 1e6
    return base * _SCALE[suf]


# ---------------------------------------------------------------------------
# Span editing
# ---------------------------------------------------------------------------

@dataclass
class _Span:
    line_idx: int
    start: int
    end: int


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _apply_span(raw_lines: list[str], span: _Span, new_value: str) -> None:
    content, ending = _split_ending(raw_lines[span.line_idx])
    if span.start < 0 or span.end > len(content) or span.start > span.end:
        raise ValueError("edit span out of range on line %d" % (span.line_idx + 1,))
    raw_lines[span.line_idx] = content[:span.start] + new_value + content[span.end:] + ending


def _append_instance_param(netlist: Netlist, inst: Instance, param: str, value: str) -> None:
    line_idx = inst.line_no - 1
    if line_idx < 0 or line_idx >= len(netlist.raw_lines):
        return
    content, ending = _split_ending(netlist.raw_lines[line_idx])
    stripped = content.rstrip()
    pad = content[len(stripped):]
    addition = " %s=%s" % (param, value)
    # Keep original trailing whitespace after the new assignment.
    netlist.raw_lines[line_idx] = stripped + addition + pad + ending
    start = len(stripped) + 1 + len(param) + 1
    spans = getattr(inst, "_param_spans", None)
    if spans is None:
        inst._param_spans = {}
        spans = inst._param_spans
    spans[param] = _Span(line_idx, start, start + len(value))


def _all_instances(netlist: Netlist) -> list[Instance]:
    out: list[Instance] = []
    seen: set[int] = set()

    def add(inst: Instance) -> None:
        key = id(inst)
        if key in seen:
            return
        seen.add(key)
        out.append(inst)

    def walk_statements(stmts: list) -> None:
        for st in stmts:
            if isinstance(st, Instance):
                add(st)
            elif isinstance(st, Subckt):
                for inst in st.instances:
                    add(inst)
                walk_statements(st.statements)

    walk_statements(netlist.statements)
    for sk in netlist.subckts.values():
        for inst in sk.instances:
            add(inst)
        walk_statements(sk.statements)
    return out


# ---------------------------------------------------------------------------
# Line / comment / depth scan
# ---------------------------------------------------------------------------

def _content(line: str) -> str:
    return _split_ending(line)[0]


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c == "_"


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c in "_.:<>[]!*#"


def _plus_cont(content: str) -> bool:
    i = 0
    n = len(content)
    while i < n and content[i] in " \t":
        i += 1
    return i < n and content[i] == "+"


class _ScanState:
    __slots__ = ("in_block", "in_string", "brace", "paren")

    def __init__(self) -> None:
        self.in_block = False
        self.in_string: str | None = None
        self.brace = 0
        self.paren = 0


def _scan_physical_line(
    content: str,
    lang: str,
    st: _ScanState,
    *,
    virt: list[str],
    vmap: list[tuple[int, int]],
    line_idx: int,
    start_col: int,
    strip_trailing_backslash: bool,
) -> bool:
    """Scan one physical line into virt/vmap. Return True if trailing `\\` continues."""
    col = start_col
    n = len(content)
    trailing_bs = False

    while col < n:
        c = content[col]

        if st.in_block:
            if c == "*" and col + 1 < n and content[col + 1] == "/":
                virt.append("*")
                vmap.append((line_idx, col))
                virt.append("/")
                vmap.append((line_idx, col + 1))
                col += 2
                st.in_block = False
                continue
            virt.append(c)
            vmap.append((line_idx, col))
            col += 1
            continue

        if st.in_string is not None:
            virt.append(c)
            vmap.append((line_idx, col))
            if c == "\\" and col + 1 < n:
                virt.append(content[col + 1])
                vmap.append((line_idx, col + 1))
                col += 2
                continue
            if c == st.in_string:
                st.in_string = None
            col += 1
            continue

        if c == "/" and col + 1 < n and content[col + 1] == "/":
            while col < n:
                virt.append(content[col])
                vmap.append((line_idx, col))
                col += 1
            break

        if c == "/" and col + 1 < n and content[col + 1] == "*":
            virt.append("/")
            vmap.append((line_idx, col))
            virt.append("*")
            vmap.append((line_idx, col + 1))
            col += 2
            st.in_block = True
            continue

        if lang == "spice" and c in "$;":
            while col < n:
                virt.append(content[col])
                vmap.append((line_idx, col))
                col += 1
            break

        if (
            strip_trailing_backslash
            and c == "\\"
            and all(ch in " \t" for ch in content[col + 1 :])
        ):
            trailing_bs = True
            break

        if c == "(":
            st.paren += 1
        elif c == ")":
            if st.paren > 0:
                st.paren -= 1
        elif c == "{":
            st.brace += 1
        elif c == "}":
            if st.brace > 0:
                st.brace -= 1
        elif c in "\"'":
            st.in_string = c

        virt.append(c)
        vmap.append((line_idx, col))
        col += 1

    return trailing_bs


def _spice_star_comment_line(content: str) -> bool:
    i = 0
    n = len(content)
    while i < n and content[i] in " \t":
        i += 1
    return i < n and content[i] == "*"


# ---------------------------------------------------------------------------
# Mask / tokens / slash-split / kv scan
# ---------------------------------------------------------------------------

@dataclass
class _Tok:
    kind: str
    value: str
    start: int
    end: int


def _mask_comments(text: str, lang: str) -> str:
    """Same-length copy with comments replaced by spaces. Strings kept."""
    out = list(text)
    n = len(text)
    i = 0
    in_block = False
    in_string: str | None = None
    line_start = True
    while i < n:
        c = text[i]
        if in_block:
            out[i] = " "
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                out[i + 1] = " "
                i += 2
                in_block = False
                line_start = False
                continue
            if c == "\n":
                line_start = True
            i += 1
            continue
        if in_string is not None:
            if c == "\\" and i + 1 < n:
                i += 2
                line_start = False
                continue
            if c == in_string:
                in_string = None
            if c == "\n":
                line_start = True
            i += 1
            continue
        if c == "\n":
            line_start = True
            i += 1
            continue
        if c in " \t":
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            in_block = True
            line_start = False
            continue
        if lang == "spice" and c == "*" and line_start:
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if lang == "spice" and c in "$;":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c in "\"'":
            in_string = c
            line_start = False
            i += 1
            continue
        line_start = False
        i += 1
    return "".join(out)


def _tokenize(masked: str, original: str | None = None) -> list[_Tok]:
    src = original if original is not None else masked
    n = len(masked)
    i = 0
    out: list[_Tok] = []
    while i < n:
        c = masked[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            out.append(_Tok("lparen", "(", i, i + 1))
            i += 1
            continue
        if c == ")":
            out.append(_Tok("rparen", ")", i, i + 1))
            i += 1
            continue
        if c == "{":
            out.append(_Tok("lbrace", "{", i, i + 1))
            i += 1
            continue
        if c == "}":
            out.append(_Tok("rbrace", "}", i, i + 1))
            i += 1
            continue
        if c == "=":
            out.append(_Tok("eq", "=", i, i + 1))
            i += 1
            continue
        if c == ",":
            out.append(_Tok("comma", ",", i, i + 1))
            i += 1
            continue
        if c in "\"'":
            q = c
            j = i + 1
            while j < n:
                if src[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if src[j] == q:
                    j += 1
                    break
                j += 1
            out.append(_Tok("string", src[i:j], i, j))
            i = j
            continue
        j = i
        while j < n and not masked[j].isspace() and masked[j] not in "(){},=":
            j += 1
        if j == i:
            out.append(_Tok("other", src[i], i, i + 1))
            i += 1
            continue
        out.append(_Tok("ident", src[i:j], i, j))
        i = j
    return out


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _looks_like_kv(text: str, j: int) -> bool:
    n = len(text)
    if j >= n or not _is_ident_start(text[j]):
        return False
    j += 1
    while j < n and _is_ident_char(text[j]):
        j += 1
    while j < n and text[j] in " \t":
        j += 1
    return j < n and text[j] == "="


def _standalone_slash(text: str, i: int) -> bool:
    prev = text[i - 1] if i > 0 else " "
    nxt = text[i + 1] if i + 1 < len(text) else " "
    if i + 1 < len(text) and text[i + 1] in "/*":
        return False
    if not (prev.isspace() and nxt.isspace()):
        return False
    # ` / ` before an operand is division, not a statement separator: ADE
    # writes CDF-computed values such as `as=(...) ? (...) : (...) / 1`.
    m = re.match(r"[ \t]*(?:[\d.(]|[A-Za-z_]\w*\()", text[i + 1:])
    return m is None


def _split_slash(text: str, lang: str = "spectre") -> list[tuple[int, int]]:
    """Return (start, end) slices of text split on standalone ` / ` at depth 0."""
    n = len(text)
    parts: list[tuple[int, int]] = []
    start = 0
    i = 0
    depth_p = 0
    depth_b = 0
    in_string: str | None = None
    in_block = False
    line_start = True
    while i < n:
        c = text[i]
        if in_block:
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                i += 2
                in_block = False
                line_start = False
                continue
            if c == "\n":
                line_start = True
            i += 1
            continue
        if in_string is not None:
            if c == "\\" and i + 1 < n:
                i += 2
                line_start = False
                continue
            if c == in_string:
                in_string = None
            if c == "\n":
                line_start = True
            i += 1
            continue
        if c == "\n":
            line_start = True
            i += 1
            continue
        if c in " \t":
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            in_block = True
            line_start = False
            continue
        if lang == "spice" and c == "*" and line_start:
            while i < n and text[i] != "\n":
                i += 1
            continue
        if lang == "spice" and c in "$;":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c in "\"'":
            in_string = c
            line_start = False
            i += 1
            continue
        if c == "(":
            depth_p += 1
        elif c == ")":
            if depth_p:
                depth_p -= 1
        elif c == "{":
            depth_b += 1
        elif c == "}":
            if depth_b:
                depth_b -= 1
        elif depth_p == 0 and depth_b == 0 and c == "/" and _standalone_slash(text, i):
            parts.append((start, i))
            start = i + 1
            i += 1
            line_start = False
            continue
        line_start = False
        i += 1
    parts.append((start, n))
    return [(a, b) for a, b in parts if text[a:b].strip()]


def _scan_value(text: str, i: int, lang: str) -> tuple[str, int, int, int]:
    """After `=`. Returns (value, val_start, val_end, next_i)."""
    n = len(text)
    while i < n and text[i] in " \t":
        i += 1
    start = i
    depth_p = 0
    depth_b = 0
    depth_k = 0
    in_string: str | None = None
    while i < n:
        c = text[i]
        if in_string is not None:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c in "\"'":
            in_string = c
            i += 1
            continue
        if c == "(":
            depth_p += 1
            i += 1
            continue
        if c == ")":
            if depth_p:
                depth_p -= 1
                i += 1
                continue
            break
        if c == "{":
            if depth_b == 0 and i != start:
                # Wrapper / body block after a finished value (sweep { ... }).
                break
            depth_b += 1
            i += 1
            continue
        if c == "}":
            if depth_b:
                depth_b -= 1
                i += 1
                continue
            break
        if c == "[":
            depth_k += 1
            i += 1
            continue
        if c == "]":
            if depth_k:
                depth_k -= 1
                i += 1
                continue
            break
        if depth_p == 0 and depth_b == 0 and depth_k == 0:
            if c == "\n":
                break
            if c == "/" and i + 1 < n and text[i + 1] in "/*":
                break
            if lang == "spice" and c in "$;":
                break
            if c == "/" and _standalone_slash(text, i):
                break
            if c == ",":
                break
            if c in " \t":
                j = i
                while j < n and text[j] in " \t":
                    j += 1
                if _looks_like_kv(text, j):
                    break
                if j < n and text[j] == "\n":
                    break
                if j < n and text[j] == "/" and _standalone_slash(text, j):
                    break
            # keep going: expressions may contain spaces (`a + b`)
        i += 1
    end = i
    while end > start and text[end - 1] in " \t":
        end -= 1
    return text[start:end], start, end, i


def _parse_kvs(
    text: str,
    start: int,
    lang: str,
    vmap: list[tuple[int, int]],
) -> tuple[dict[str, str], dict[str, _Span]]:
    params: dict[str, str] = {}
    spans: dict[str, _Span] = {}
    n = len(text)
    i = start
    in_block = False
    in_string: str | None = None
    while i < n:
        c = text[i]
        if in_block:
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                i += 2
                in_block = False
                continue
            i += 1
            continue
        if in_string is not None:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            break
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            in_block = True
            continue
        if lang == "spice" and c in "$;":
            break
        if c == "/" and _standalone_slash(text, i):
            break
        if c in ")}":
            break
        if c in "\"'":
            in_string = c
            i += 1
            continue
        if not _looks_like_kv(text, i):
            i += 1
            continue
        ns = i
        i += 1
        while i < n and _is_ident_char(text[i]):
            i += 1
        name = text[ns:i]
        while i < n and text[i] in " \t":
            i += 1
        if i >= n or text[i] != "=":
            continue
        i += 1
        val, vs, ve, i = _scan_value(text, i, lang)
        params[name] = val
        if 0 <= vs < ve <= len(vmap):
            a_line, a_col = vmap[vs]
            b_line, b_col = vmap[ve - 1]
            if a_line == b_line:
                spans[name] = _Span(a_line, a_col, b_col + 1)
    return params, spans


def _match_paren(text: str, open_idx: int) -> int:
    """Return index of the matching close paren, or -1."""
    n = len(text)
    depth = 0
    in_string: str | None = None
    i = open_idx
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c in "\"'":
            in_string = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_ws_items(text: str) -> list[str]:
    """Split on whitespace/commas at depth 0. Keeps `name=value` as one item."""
    items: list[str] = []
    buf: list[str] = []
    depth_p = 0
    depth_b = 0
    in_string: str | None = None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            buf.append(c)
            if c == "\\" and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c in "\"'":
            in_string = c
            buf.append(c)
            i += 1
            continue
        if c == "(":
            depth_p += 1
            buf.append(c)
            i += 1
            continue
        if c == ")":
            if depth_p:
                depth_p -= 1
            buf.append(c)
            i += 1
            continue
        if c == "{":
            depth_b += 1
            buf.append(c)
            i += 1
            continue
        if c == "}":
            if depth_b:
                depth_b -= 1
            buf.append(c)
            i += 1
            continue
        if depth_p == 0 and depth_b == 0 and (c.isspace() or c == ","):
            if buf:
                items.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        items.append("".join(buf))
    return items


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, text: str) -> None:
        self.raw_lines = text.splitlines(keepends=True)
        self.contents = [_content(ln) for ln in self.raw_lines]
        self.n = len(self.raw_lines)
        self.i = 0
        self.start_lang = _detect_start_lang(self.contents)
        self.lang = self.start_lang
        self.title = _detect_title(self.contents)

        self.statements: list = []
        self.subckts: dict[str, Subckt] = {}
        self.parameters: dict[str, str] = {}
        self.includes: list[Include] = []
        self.analyses: list[Analysis] = []
        self.options: dict[str, str] = {}
        self.saves: list[str] = []
        self.param_spans: dict[str, _Span] = {}

    def parse(self) -> Netlist:
        self._parse_block(self.statements, self.subckts, self.parameters, None, stop_on_ends=False)
        nl = Netlist(
            title=self.title,
            language=self.start_lang,
            statements=self.statements,
            subckts=self.subckts,
            parameters=self.parameters,
            includes=self.includes,
            analyses=self.analyses,
            options=self.options,
            saves=self.saves,
            raw_lines=self.raw_lines,
        )
        nl._param_spans = self.param_spans
        return nl

    def _collect(self) -> _Logical | None:
        if self.i >= self.n:
            return None
        st = _ScanState()
        virt: list[str] = []
        vmap: list[tuple[int, int]] = []
        indices: list[int] = []
        first = True
        start_line = self.i

        while self.i < self.n:
            content = self.contents[self.i]
            indices.append(self.i)
            col = 0
            n = len(content)

            if not first:
                ws = 0
                while ws < n and content[ws] in " \t":
                    ws += 1
                if ws < n and content[ws] == "+":
                    virt.append(" ")
                    vmap.append((self.i, ws))
                    col = ws + 1

            trailing_bs = _scan_physical_line(
                content,
                self.lang,
                st,
                virt=virt,
                vmap=vmap,
                line_idx=self.i,
                start_col=col,
                strip_trailing_backslash=True,
            )

            more = (
                st.in_block
                or st.in_string is not None
                or st.brace > 0
                or st.paren > 0
                or trailing_bs
            )
            self.i += 1
            first = False
            if not more and self.i < self.n and _plus_cont(self.contents[self.i]):
                more = True
            if not more:
                break

            # Brace / paren / comment continuation: keep a newline so inner
            # statements in wrapper blocks stay separable.
            if not trailing_bs and (
                st.in_block or st.in_string is not None or st.brace > 0 or st.paren > 0
            ):
                virt.append("\n")
                vmap.append((self.i - 1, len(self.contents[self.i - 1])))

        return _Logical("".join(virt), vmap, start_line + 1, indices)

    def _parse_block(
        self,
        statements: list,
        subckts: dict[str, Subckt],
        params: dict[str, str],
        param_spans: dict[str, _Span] | None,
        stop_on_ends: bool,
    ) -> None:
        queue: list[_Logical] = []

        def refill() -> bool:
            if self.i >= self.n:
                return False
            logical = self._collect()
            if logical is None:
                return False
            queue.extend(self._parts(logical))
            return True

        while True:
            if not queue:
                if not refill():
                    return
                continue
            part = queue.pop(0)
            kind = self._classify(part)
            if kind == "empty":
                continue
            if kind == "ends":
                if stop_on_ends:
                    return
                continue
            if kind == "subckt":
                sk = self._parse_subckt(part, queue)
                statements.append(sk)
                subckts[sk.name] = sk
                continue
            self._dispatch(part, kind, statements, params, param_spans)

    def _parts(self, logical: _Logical) -> list[_Logical]:
        if self.lang == "spice" and _spice_star_comment_line(logical.text):
            return [logical]
        if not _mask_comments(logical.text, self.lang).strip():
            return [logical]
        spans = _split_slash(logical.text, self.lang)
        if not spans:
            return [logical]
        out: list[_Logical] = []
        for a, b in spans:
            out.append(
                _Logical(
                    logical.text[a:b],
                    logical.vmap[a:b],
                    logical.line_no,
                    logical.line_indices,
                )
            )
        return out

    def _classify(self, part: _Logical) -> str:
        masked = _mask_comments(part.text, self.lang)
        if not masked.strip():
            return "empty"
        toks = _tokenize(masked, part.text)
        if not toks:
            return "empty"
        w0 = toks[0].value
        low = w0.lower()
        if self.lang == "spice" and _spice_star_comment_line(part.text):
            return "empty"
        if low in (".ends", "ends"):
            return "ends"
        if low in (".subckt", "subckt"):
            return "subckt"
        if low == "inline" and len(toks) >= 2 and toks[1].value.lower() == "subckt":
            return "subckt"
        if low.startswith("."):
            return "spice_card"
        if low == "simulator":
            return "simulator"
        if low in ("parameters", "parameter"):
            return "parameters"
        if low in ("include", "ahdl_include"):
            return "include"
        if low == "model":
            return "model"
        if low in ("save", "saves"):
            return "save"
        if low in ("options", "option", "set"):
            return "options"
        if low == "global":
            return "global"
        if len(toks) >= 2 and toks[1].value.lower() in ("options", "option"):
            return "named_options"
        if len(toks) >= 2 and toks[1].value.lower() in ANALYSIS_TYPES:
            return "named_analysis"
        if low in ANALYSIS_TYPES:
            return "analysis"
        return "instance"

    def _dispatch(
        self,
        part: _Logical,
        kind: str,
        statements: list,
        params: dict[str, str],
        param_spans: dict[str, _Span] | None,
    ) -> None:
        if kind == "simulator":
            self._do_simulator(part)
        elif kind == "parameters":
            self._do_parameters(part, params, param_spans)
        elif kind == "include":
            self._do_include(part, statements, "include")
        elif kind == "model":
            self._do_model(part, statements)
        elif kind == "save":
            self._do_save(part)
        elif kind == "options":
            self._do_options(part)
        elif kind == "named_options":
            self._do_options(part, skip_name=True)
        elif kind == "named_analysis":
            self._do_analysis(part, named=True, statements=statements)
        elif kind == "analysis":
            self._do_analysis(part, named=False, statements=statements)
        elif kind == "spice_card":
            self._do_spice_card(part, statements, params, param_spans)
        elif kind == "instance":
            inst = self._do_instance(part)
            if inst is not None:
                statements.append(inst)
        elif kind == "global":
            return

    def _do_simulator(self, part: _Logical) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        start = toks[1].start if len(toks) > 1 else len(part.text)
        kv, _ = _parse_kvs(part.text, start, self.lang, part.vmap)
        lang = kv.get("lang") or kv.get("language")
        if lang:
            lang = _unquote(lang).strip().lower()
            if lang in ("spice", "spectre"):
                self.lang = lang

    def _do_parameters(
        self,
        part: _Logical,
        params: dict[str, str],
        param_spans: dict[str, _Span] | None,
    ) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        start = toks[0].end if toks else 0
        kv, spans = _parse_kvs(part.text, start, self.lang, part.vmap)
        params.update(kv)
        dest = param_spans if param_spans is not None else self.param_spans
        dest.update(spans)

    def _do_include(self, part: _Logical, statements: list, default_kind: str) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        if not toks:
            return
        w0 = toks[0].value.lower()
        kind = default_kind
        if w0 in ("ahdl_include", ".ahdl_include"):
            kind = "ahdl_include"
        elif w0 in (".lib", "lib"):
            kind = "lib"
        elif w0 in ("include", ".include", ".inc"):
            kind = "include"
        path = ""
        section = None
        rest_start = toks[0].end
        if len(toks) >= 2:
            path = _unquote(toks[1].value)
            rest_start = toks[1].end
        kv, _ = _parse_kvs(part.text, rest_start, self.lang, part.vmap)
        if "section" in kv:
            section = _unquote(kv["section"])
        elif kind == "lib" and len(toks) >= 3 and toks[2].kind != "eq":
            # .lib path section
            if "=" not in toks[2].value:
                section = _unquote(toks[2].value)
        inc = Include(path=path, section=section, kind=kind)
        self.includes.append(inc)
        statements.append(inc)

    def _do_model(self, part: _Logical, statements: list) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        # model NAME TYPE ...
        idx = 0
        if toks and toks[0].value.lower() in ("model", ".model"):
            idx = 1
        name = toks[idx].value if len(toks) > idx else ""
        idx += 1
        mtype = toks[idx].value if len(toks) > idx else ""
        idx += 1
        start = toks[idx].start if len(toks) > idx else len(part.text)
        # Optional paren / brace wrapper around params.
        kv, _ = self._params_maybe_wrapped(part, start)
        mdl = Model(name=name, mtype=mtype, params=kv, line_no=part.line_no, raw=part.text)
        statements.append(mdl)

    def _params_maybe_wrapped(self, part: _Logical, start: int) -> tuple[dict[str, str], dict[str, _Span]]:
        i = start
        n = len(part.text)
        while i < n and part.text[i].isspace():
            i += 1
        if i < n and part.text[i] in "{(":
            close = "}" if part.text[i] == "{" else ")"
            inner_s = i + 1
            depth = 1
            j = inner_s
            in_string: str | None = None
            while j < n and depth:
                c = part.text[j]
                if in_string:
                    if c == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if c == in_string:
                        in_string = None
                    j += 1
                    continue
                if c in "\"'":
                    in_string = c
                elif c == part.text[i]:
                    depth += 1
                elif c == close:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            return _parse_kvs(part.text, inner_s, self.lang, part.vmap)
        return _parse_kvs(part.text, i, self.lang, part.vmap)

    def _do_save(self, part: _Logical) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        for t in toks[1:]:
            if t.kind == "eq":
                break
            self.saves.append(_unquote(t.value))
        kv, _ = _parse_kvs(part.text, toks[0].end if toks else 0, self.lang, part.vmap)
        if "save" in kv:
            for piece in kv["save"].replace(",", " ").split():
                if piece and piece not in self.saves:
                    self.saves.append(piece)

    def _do_options(self, part: _Logical, skip_name: bool = False) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        if not toks:
            return
        start = toks[0].end
        if skip_name and len(toks) >= 2:
            start = toks[1].end
        kv, _ = _parse_kvs(part.text, start, self.lang, part.vmap)
        self.options.update(kv)

    def _do_analysis(self, part: _Logical, named: bool, statements: list) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        if not toks:
            return
        if named:
            name = toks[0].value
            atype = toks[1].value.lower()
            start = toks[1].end
        else:
            atype = toks[0].value.lower()
            name = atype
            start = toks[0].end
        kv, _ = _parse_kvs(part.text, start, self.lang, part.vmap)
        if not kv:
            rest = _mask_comments(part.text[start:], self.lang).strip()
            if rest and not rest.startswith("{"):
                kv = {"_args": rest}
        ana = Analysis(name=name, type=atype, params=kv, line_no=part.line_no, raw=part.text)
        self.analyses.append(ana)
        statements.append(ana)
        if atype in WRAPPER_TYPES:
            self._harvest_inner_analyses(part)

    def _harvest_inner_analyses(self, part: _Logical) -> None:
        text = part.text
        brace = text.find("{")
        if brace < 0:
            return
        depth = 0
        end = -1
        in_string: str | None = None
        j = brace
        n = len(text)
        while j < n:
            c = text[j]
            if in_string:
                if c == "\\" and j + 1 < n:
                    j += 2
                    continue
                if c == in_string:
                    in_string = None
                j += 1
                continue
            if c in "\"'":
                in_string = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if end < 0:
            return
        inner = text[brace + 1 : end]
        for chunk in _split_inner_lines(inner):
            vmap = [(part.line_no - 1, 0)] * len(chunk)
            sub = _Logical(chunk, vmap, part.line_no, part.line_indices)
            kind = self._classify(sub)
            if kind in ("named_analysis", "analysis"):
                self._do_analysis(sub, named=(kind == "named_analysis"), statements=[])

    def _do_spice_card(
        self,
        part: _Logical,
        statements: list,
        params: dict[str, str],
        param_spans: dict[str, _Span] | None,
    ) -> None:
        toks = _tokenize(_mask_comments(part.text, self.lang), part.text)
        if not toks:
            return
        card = toks[0].value.lower()
        if card in (".include", ".inc"):
            self._do_include(part, statements, "include")
        elif card == ".lib":
            self._do_include(part, statements, "lib")
        elif card == ".ahdl_include":
            self._do_include(part, statements, "ahdl_include")
        elif card in (".param", ".parameters", ".parameter"):
            kv, spans = _parse_kvs(part.text, toks[0].end, self.lang, part.vmap)
            params.update(kv)
            dest = param_spans if param_spans is not None else self.param_spans
            dest.update(spans)
        elif card == ".model":
            self._do_model(part, statements)
        elif card in (".option", ".options"):
            self._do_options(part)
        elif card in (".save", ".probe"):
            self._do_save(part)
        elif card.startswith(".") and card[1:] in ANALYSIS_TYPES:
            name = card[1:]
            kv, _ = _parse_kvs(part.text, toks[0].end, self.lang, part.vmap)
            if not kv:
                rest = _mask_comments(part.text[toks[0].end :], self.lang).strip()
                if rest:
                    kv = {"_args": rest}
            ana = Analysis(name=name, type=name, params=kv, line_no=part.line_no, raw=part.text)
            self.analyses.append(ana)
            statements.append(ana)

    def _do_instance(self, part: _Logical) -> Instance | None:
        masked = _mask_comments(part.text, self.lang)
        toks = _tokenize(masked, part.text)
        if not toks:
            return None
        name = toks[0].value
        rest = toks[1:]
        nodes: list[str] = []
        master = ""
        param_start = len(part.text)

        if rest and rest[0].kind == "lparen":
            close_at = _match_paren(part.text, rest[0].start)
            if close_at < 0:
                close_at = rest[0].end
            nodes = _split_ws_items(part.text[rest[0].start + 1 : close_at])
            after = [t for t in rest if t.start > close_at]
            if after:
                master = after[0].value
                param_start = after[0].end
            else:
                param_start = close_at + 1
        else:
            first_kv = None
            for i, t in enumerate(rest):
                if t.kind == "ident" and i + 1 < len(rest) and rest[i + 1].kind == "eq":
                    first_kv = i
                    break
                if t.kind == "eq" and i > 0:
                    first_kv = i - 1
                    break
            head = rest[:first_kv] if first_kv is not None else rest
            spice_passive = (
                self.lang == "spice"
                and _spice_letter(name) in _SPICE_PASSIVE
            )
            if spice_passive:
                master, pkey = _SPICE_PASSIVE[_spice_letter(name)]
                pos = [t for t in head if t.kind != "comma"]
                if len(pos) >= 3:
                    nodes = [t.value for t in pos[:-1]]
                    pos_val = pos[-1]
                    param_start = pos_val.end if first_kv is None else rest[first_kv].start
                    kv, spans = _parse_kvs(part.text, param_start, self.lang, part.vmap)
                    if pkey not in kv:
                        kv[pkey] = pos_val.value
                        if 0 <= pos_val.start < pos_val.end <= len(part.vmap):
                            a_line, a_col = part.vmap[pos_val.start]
                            b_line, b_col = part.vmap[pos_val.end - 1]
                            if a_line == b_line:
                                spans[pkey] = _Span(a_line, a_col, b_col + 1)
                    inst = Instance(
                        name=name,
                        nodes=nodes,
                        master=master,
                        params=kv,
                        line_no=part.line_no,
                        raw=part.text,
                    )
                    inst._param_spans = spans
                    return inst
                nodes = [t.value for t in pos]
                param_start = pos[-1].end if pos and first_kv is None else (
                    rest[first_kv].start if first_kv is not None else len(part.text)
                )
            elif first_kv is None:
                if rest:
                    master = rest[-1].value
                    nodes = [t.value for t in rest[:-1] if t.kind != "comma"]
                    param_start = rest[-1].end
            else:
                if head:
                    master = head[-1].value
                    nodes = [t.value for t in head[:-1] if t.kind != "comma"]
                param_start = rest[first_kv].start

        kv, spans = _parse_kvs(part.text, param_start, self.lang, part.vmap)
        inst = Instance(
            name=name,
            nodes=nodes,
            master=master,
            params=kv,
            line_no=part.line_no,
            raw=part.text,
        )
        inst._param_spans = spans
        return inst

    def _parse_subckt(self, header: _Logical, queue: list[_Logical]) -> Subckt:
        name, ports, hdr_params, hdr_spans = _parse_subckt_header(header, self.lang)
        body_params: dict[str, str] = dict(hdr_params)
        body_spans: dict[str, _Span] = dict(hdr_spans)
        body_statements: list = []
        body_subckts: dict[str, Subckt] = {}

        def refill() -> bool:
            if self.i >= self.n:
                return False
            logical = self._collect()
            if logical is None:
                return False
            queue.extend(self._parts(logical))
            return True

        while True:
            if not queue:
                if not refill():
                    break
                continue
            part = queue.pop(0)
            kind = self._classify(part)
            if kind == "empty":
                continue
            if kind == "ends":
                break
            if kind == "subckt":
                sk = self._parse_subckt(part, queue)
                body_statements.append(sk)
                body_subckts[sk.name] = sk
                continue
            self._dispatch(part, kind, body_statements, body_params, body_spans)

        instances = [st for st in body_statements if isinstance(st, Instance)]
        sk = Subckt(
            name=name,
            ports=ports,
            params=body_params,
            instances=instances,
            statements=body_statements,
            line_no=header.line_no,
        )
        self.subckts[sk.name] = sk
        return sk


@dataclass
class _Logical:
    text: str
    vmap: list[tuple[int, int]]
    line_no: int
    line_indices: list[int]


def _parse_subckt_header(
    part: _Logical, lang: str
) -> tuple[str, list[str], dict[str, str], dict[str, _Span]]:
    toks = _tokenize(_mask_comments(part.text, lang), part.text)
    idx = 0
    if toks and toks[0].value.lower() == "inline":
        idx = 1
    if idx < len(toks) and toks[idx].value.lower() in ("subckt", ".subckt"):
        idx += 1
    name = toks[idx].value if idx < len(toks) else ""
    idx += 1
    ports: list[str] = []
    param_start = len(part.text)
    rest = toks[idx:]
    if rest and rest[0].kind == "lparen":
        depth = 0
        close_i = 0
        for close_i, t in enumerate(rest):
            if t.kind == "lparen":
                depth += 1
                if depth == 1:
                    continue
            if t.kind == "rparen":
                depth -= 1
                if depth == 0:
                    break
            if t.kind == "comma":
                continue
            if depth >= 1:
                ports.append(t.value)
        after = rest[close_i + 1 :]
        param_start = rest[close_i].end
        if after:
            if after[0].value.lower() in ("parameters", "parameter"):
                param_start = after[0].end
            else:
                param_start = after[0].start
    else:
        i = 0
        while i < len(rest):
            t = rest[i]
            if t.value.lower() in ("parameters", "parameter"):
                param_start = t.end
                break
            if t.kind == "ident" and i + 1 < len(rest) and rest[i + 1].kind == "eq":
                param_start = t.start
                break
            if t.kind == "eq" and i > 0:
                param_start = rest[i - 1].start
                break
            if t.kind == "lbrace":
                param_start = t.start
                break
            if t.kind != "comma":
                ports.append(t.value)
            i += 1
        else:
            if rest:
                param_start = rest[-1].end
    kv, spans = _parse_kvs(part.text, param_start, lang, part.vmap)
    return name, ports, kv, spans


def _split_inner_lines(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    depth_p = 0
    depth_b = 0
    in_string: str | None = None
    in_block = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_block:
            buf.append(c)
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                buf.append("/")
                i += 2
                in_block = False
                continue
            i += 1
            continue
        if in_string:
            buf.append(c)
            if c == "\\" and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            buf.append("/")
            buf.append("*")
            i += 2
            in_block = True
            continue
        if c == "\n" and depth_p == 0 and depth_b == 0:
            chunk = "".join(buf)
            if chunk.strip():
                out.append(chunk)
            buf = []
            i += 1
            continue
        if c in "\"'":
            in_string = c
        elif c == "(":
            depth_p += 1
        elif c == ")":
            if depth_p:
                depth_p -= 1
        elif c == "{":
            depth_b += 1
        elif c == "}":
            if depth_b:
                depth_b -= 1
        buf.append(c)
        i += 1
    chunk = "".join(buf)
    if chunk.strip():
        out.append(chunk)
    return out


def _detect_title(contents: list[str]) -> str:
    for c in contents:
        s = c.strip()
        if not s:
            continue
        if s.startswith("//"):
            return s[2:].strip()
        if s.startswith("/*"):
            body = s[2:]
            if "*/" in body:
                body = body[: body.index("*/")]
            return body.strip()
        if s.startswith("*"):
            return s[1:].strip()
        return ""
    return ""


def _detect_start_lang(contents: list[str]) -> str:
    for c in contents:
        s = c.strip()
        if not s:
            continue
        if s.startswith("//") or s.startswith("/*"):
            continue
        if s.startswith("*"):
            return "spice"
        low = s.lower()
        if low.startswith("simulator"):
            return "spectre"
        if s.startswith("."):
            return "spice"
        return "spectre"
    return "spectre"
