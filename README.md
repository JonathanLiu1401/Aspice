# aspice

Research codebase for the EMiT Lab (UW ECE) on LLM agents that design analog CMOS in
Cadence Virtuoso: a schematic a person can read, a layout a working designer would ship,
and a parasitic extraction that still meets spec.

This is a group project, not a personal hobby wrapper around PySpice and not an
auto-layout button. The agent writes the generator. The code under `lib/` checks each
step and keeps netlists small enough to fit in a context window. The rules the agent
must follow are [`AGENTS.md`](AGENTS.md) (loaded from [`CLAUDE.md`](CLAUDE.md)).

The benchmark that runs those agents is a separate repository, not part of this package.
The layout method follows LAYGO (DOI 10.1109/TCSI.2020.3046524) and BAG3++ (DOI 10.1109/OJCAS.2024.3502641). Those papers are not redistributed here.
The flow those papers and [laygo2](https://laygo2.github.io/overview.html) describe is
implemented here by `lib/laygrid.py`.

## What an agent is required to do

- Draw schematics by hand, wires between terminals, not a grid of net labels.
- Prove the operating point (Razavi regions) with `scripts/opcheck.py` before layout.
- Generate layout from PDK pcells on a metal grid. One Python file per cell. No bond
  pads on a block. No draw-and-resimulate loop. Search
  `scripts/layout_search.py` before inventing a structure.
- Sign off with `scripts/verify.py` (DRC, LVS, Quantus, pre vs post).
- Read `scripts/netbrief.py`, never a raw netlist.

## What is in the repo

| Piece | Role |
|---|---|
| `AGENTS.md` | Project rules. Read this first |
| `SKILL.md` | Claude Code skill: schematic, operating point, layout, sign-off |
| `references/layout-kb/` | Searchable layout technique cards, citing Hastings, Saint & Saint, LAYGO, BAG3++, laygo2. Not the books themselves |
| `lib/laygrid.py`, `lib/layaudit.py` | Template-and-grid layout generator and the floorplan audit |
| `lib/aspverify/` , `scripts/verify.py` | Batch DRC, LVS, Quantus, pre/post comparison |
| `lib/opcheck.py` | Spectre operating-point check |
| `lib/netlist_digest.py`, `lib/netbrief.py` | Compact schematic and extracted-netlist views |
| `lib/analog_spice.py` and the Spectre parser | Hand analysis and ADE netlists, including on a machine without Cadence |
| `lib/cadence.py` | Headless Spectre, Virtuoso, OCEAN, Calibre, on a machine that has them |

`student` branch adds a class mode that refuses to do topology and sizing for a student.
Layout and sign-off rules are the same on both branches.

## Setup

Python 3, PySpice 1.4.3, ngspice >= 33 (tested on 44 and 47). On the lab account the
venv is `~/.claude/skills-venv`.

```bash
python -m venv .venv
.venv/bin/python -m pip install "PySpice==1.4.3"
python scripts/fetch_models.py      # PTM cards; not a foundry PDK
```

```bash
python scripts/analog_selftest.py
python scripts/test_parser.py
python scripts/test_digest.py
python scripts/test_opcheck.py
python scripts/test_laygrid.py
python scripts/test_layaudit.py
python scripts/test_netbrief.py
python scripts/test_layout_search.py
python scripts/test_verify.py       # needs the bridge package for the host tests
```

Layout search uses the technique cards by keyword with no extra install. To add semantic
rank, with [Ollama](https://ollama.com/) already running locally:

```bash
ollama pull qwen3-embedding:8b
python scripts/layout_search.py --build
python scripts/layout_search.py "shield the CDAC top plate"
```

## Layout method (short)

1. Floorplan from the technique cards. Hastings for matching, laygo2 / LAYGO for the grid.
2. `layout_<cell>.py` places PDK pcells and routes on tracks.
3. One DRC, fix the script, extract the fast leaves, then the block.
4. The ADC is not a chip: no bond pads.

`references/laygrid.md` and `AGENTS.md` are the full version.

## Netlists

```python
import sys; sys.path.insert(0, 'lib')
import spectre_netlist as SN, netlist_digest as ND
nl = SN.parse_file('design.scs')
print(ND.outline(nl))
```

Or, without opening the file in the conversation:

```bash
python scripts/netbrief.py scs design.scs
python scripts/netbrief.py pex extracted.sp
```

## Cadence on the lab machines

Heavy Cadence I/O on NFS has stalled hosts. Run Spectre, DRC, LVS, and Quantus on a
local copy under `/var/tmp`, never on an NFS design, and never write `~/EE332`,
`~/EE476`, `~/simulation`, or `~/EMiT/cadence` unless the user asked. Kill Cadence by
PID. Never `kill -9` a process in D state, and never `pkill -f` with a pattern that
matches the command itself.

`scripts/verify.py` is the sign-off path. `lib/cadence.py` is the headless path for
reading and simulating a cell. From a machine without Cadence, `scripts/lab_run.py`
runs a deck on a lab host.

## Things the tools already encode

- ngspice reads a trailing `A` as atto and `F` as femto. `lint_netlist` refuses that.
- fT from reported capacitances ignores overlap; `f_t()` measures it.
- In Spectre `M` is mega; in SPICE it is milli.
- A PDK `w` is a finger width in one kit and a total width in another. Size as finger width and finger count.
- `gnd` is not ground in Spectre. The translator renames it.
- The same BSIM4 card agrees across Spectre and ngspice to about 0.05 %. PTM-45nm and gpdk045 at the same bias do not. A PTM number is not a gpdk045 prediction.

## Licence

MIT, see `LICENSE`. PTM cards are downloaded at setup and are not covered. The technique
cards cite textbooks and papers; those works are not included.
