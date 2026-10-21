"""Physical-verification settings: tool commands, rule decks and Quantus corners.

Nothing here knows a PDK. Every path comes from the profile's ``.env`` so the same
code serves gpdk045, sky130 or a foundry kit:

    VB_PV_DRC_RULES      PVS DRC rule deck
    VB_PV_LVS_RULES      PVS LVS rule deck (must allow Quantus data: ``-rc_data``)
    VB_PV_RUN_FILES      files the decks include from the run directory, comma separated
                         (e.g. a ``pvs_control_file``); copied into every run
    VB_QRC_CORNERS       Quantus corners, ``name=techdir`` comma separated
                         (e.g. ``typical=/kit/qrc/typical,rcworst=/kit/qrc/rcworst``)
    VB_PV_MODELS         Spectre model files for post-layout runs, ``file[@section]``
                         separated by ``;``
    VB_PV_CDS_LIB        cds.lib that defines the design libraries (batch export)
    VB_PV_WORK_DIR       work directory on the verification host
    VB_PVS_BIN / VB_QRC_BIN / VB_SI_BIN / VB_STRMOUT_BIN / VB_SPECTRE_BIN
                         tool commands (default: the bare names on PATH)

Each name also accepts a ``_<profile>`` suffix that overrides the global value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from virtuoso_bridge.env import load_vb_env


def _split(text: str, sep: str = ",") -> tuple[str, ...]:
    return tuple(p.strip() for p in text.split(sep) if p.strip())


def parse_corners(text: str) -> dict[str, str]:
    """``"typical=/a,rcworst=/b"`` -> ``{"typical": "/a", "rcworst": "/b"}``."""
    corners: dict[str, str] = {}
    for item in _split(text):
        name, sep, path = item.partition("=")
        if not sep or not name.strip() or not path.strip():
            raise ValueError(f"VB_QRC_CORNERS entry {item!r} is not name=techdir")
        corners[name.strip()] = path.strip()
    return corners


def parse_models(text: str) -> tuple[tuple[str, str | None], ...]:
    """``"/m/a.scs@tt;/m/b.scs"`` -> ``(("/m/a.scs", "tt"), ("/m/b.scs", None))``."""
    out = []
    for item in _split(text, ";"):
        path, sep, section = item.partition("@")
        out.append((path.strip(), (section.strip() or None) if sep else None))
    return tuple(out)


@dataclass(frozen=True)
class VerifyConfig:
    drc_rules: str = ""
    lvs_rules: str = ""
    run_files: tuple[str, ...] = ()
    qrc_corners: dict[str, str] = field(default_factory=dict)
    models: tuple[tuple[str, str | None], ...] = ()
    cds_lib: str = ""
    work_dir: str = ""
    pvs: str = "pvs"
    qrc: str = "qrc"
    si: str = "si"
    strmout: str = "strmout"
    spectre: str = "spectre"
    license_timeout: int = 300

    @classmethod
    def from_env(cls, profile: str | None = None) -> VerifyConfig:
        load_vb_env()
        suffix = f"_{profile}" if profile else ""

        def get(name: str, default: str = "") -> str:
            return (os.environ.get(name + suffix, "").strip()
                    or os.environ.get(name, "").strip() or default)

        user = os.environ.get("USER", "user")
        return cls(
            drc_rules=get("VB_PV_DRC_RULES"),
            lvs_rules=get("VB_PV_LVS_RULES"),
            run_files=_split(get("VB_PV_RUN_FILES")),
            qrc_corners=parse_corners(get("VB_QRC_CORNERS")),
            models=parse_models(get("VB_PV_MODELS")),
            cds_lib=get("VB_PV_CDS_LIB"),
            work_dir=get("VB_PV_WORK_DIR", f"/tmp/{user}/vb_verify"),
            pvs=get("VB_PVS_BIN", "pvs"),
            qrc=get("VB_QRC_BIN", "qrc"),
            si=get("VB_SI_BIN", "si"),
            strmout=get("VB_STRMOUT_BIN", "strmout"),
            spectre=get("VB_SPECTRE_BIN", "spectre"),
            license_timeout=int(get("VB_PV_LICENSE_TIMEOUT", "300")),
        )

    def missing(self, steps: tuple[str, ...]) -> list[str]:
        """Names of settings the requested steps need but are not set."""
        need = []
        if {"gds", "cdl"} & set(steps) and not self.cds_lib:
            need.append("VB_PV_CDS_LIB (or --cds-lib)")
        if "drc" in steps and not self.drc_rules:
            need.append("VB_PV_DRC_RULES")
        if {"lvs", "pex"} & set(steps) and not self.lvs_rules:
            need.append("VB_PV_LVS_RULES")
        if "pex" in steps and not self.qrc_corners:
            need.append("VB_QRC_CORNERS")
        if "post" in steps and not self.models:
            need.append("VB_PV_MODELS")
        return need
