"""Track categorisation based on VitalDB track names only.

Categories are *name-based heuristics* for navigation. They are not a
verified description of what a track measures. Drug tracks in particular get
a cautious "semantic" label that is only specific where the VitalDB track
naming convention makes it explicit (e.g. ``Orchestra/PPF20_CE`` is the
Orchestra pump-reported effect-site concentration for propofol 2%).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CAT_HAEMO = "Haemodynamics"
CAT_OXY = "Oxygenation"
CAT_DEPTH = "Anaesthetic depth / EEG"
CAT_RESP = "Respiratory"
CAT_DRUG = "Anaesthetic delivery"
CAT_NMT = "Neuromuscular"
CAT_TEMP = "Temperature"
CAT_FLUID = "Fluids / urine"
CAT_MISC = "Events / other"

CATEGORY_ORDER = [CAT_HAEMO, CAT_OXY, CAT_DEPTH, CAT_RESP, CAT_DRUG,
                  CAT_NMT, CAT_TEMP, CAT_FLUID, CAT_MISC]


def _short(track_name: str) -> str:
    """Part of the name after the device prefix, e.g. 'Solar8000/HR' -> 'HR'."""
    return track_name.split("/", 1)[-1]


# Ordered (first match wins). Patterns run on the short name, case-insensitive.
_RULES: list[tuple[str, re.Pattern]] = [
    (CAT_DRUG, re.compile(
        r"^(PPF|RFTN|PROP|REMI|FEN|MIDA|ROC|VEC|EPH|PHE|NEPI|NE|EPI|DOPA|DOBU|VASO|NTG|NIC|ESM|"
        r"LABE|ATRO|CIS|NEO|SUX|KET|DEX|MILR|OXY|VAS|NOR)\w*_(CE|CP|CT|VOL|RATE|CONC|\w*)$"
        r"|PPF20|RFTN20|RFTN50|(^|_)(SEVO|DES|ISO|N2O)(_|$)|^(INSP|EXP|FI|FE)_?(SEVO|DES|ISO|AGENT)|"
        r"^(INSP|EXP)_(SEVO|DES)", re.I)),
    (CAT_DEPTH, re.compile(r"^(BIS|EEG|SEF|SR|EMG|SQI|TOTPOW|SSI|PSI|ENTROPY|RE$|SE$)|(^|_)EEG|BIS", re.I)),
    (CAT_NMT, re.compile(r"^(TOF|NMT|PTC|TWITCH)|(^|_)TOF", re.I)),
    (CAT_OXY, re.compile(r"(^|_)(SPO2|PLETH|SAT)|PLETH|SPO2|^PI$|^PVI$|^SV$|^SVI$", re.I)),
    (CAT_RESP, re.compile(
        r"(^|_)(RR|RESP|VENT|TV|MV|PEEP|PIP|PPLAT|MAWP|MAC|FIO2|ETCO2|FICO2|CO2|MINVOL|COMPLIANCE|"
        r"AIRWAY|INSP|EXP|AWP|PAW|O2|N2O|AIR)(_|$)|^(ETCO2|FIO2|RR|TV|MV|PEEP|PIP)|CAPNO|CO2", re.I)),
    (CAT_HAEMO, re.compile(
        r"(^|_)(HR|ECG|ART|ABP|NIBP|IBP|SBP|DBP|MBP|MAP|CVP|PA|PAP|CO|CI|SVV|SVR|ST|PVC|QT|BP|NIBP_SBP|"
        r"NIBP_DBP|NIBP_MBP|ARTERIAL|PULSE)(_|$)|ECG|^ART|NIBP|^CVP|^PA_", re.I)),
    (CAT_TEMP, re.compile(r"(^|_)(TEMP|BT|TEMPERATURE|NTP|TEMP_ESOPH|TMP)(_|$)|TEMP", re.I)),
    (CAT_FLUID, re.compile(r"(^|_)(URINE|UO|UOP|BLOOD_LOSS|EBL|INTAKE|OUTPUT|FLUID|IV|BAL)(_|$)|URINE", re.I)),
]


def categorise(track_name: str) -> str:
    """Return a navigation category for a track name (heuristic)."""
    if track_name.split("/", 1)[0].lower() == "orchestra":
        return CAT_DRUG          # the Orchestra pump device only carries infusion tracks
    short = _short(track_name)
    for cat, rx in _RULES:
        if rx.search(short) or rx.search(track_name):
            return cat
    return CAT_MISC


# ---------------------------------------------------------------------------
# Drug track semantics
# ---------------------------------------------------------------------------

DRUG_SEMANTIC_UNKNOWN = "Drug-related track (meaning not verified)"


@dataclass(frozen=True)
class DrugInfo:
    drug: str          # human drug name, or "" if unknown
    kind: str          # e.g. "Effect-site concentration"
    verified: bool     # True only for tracks defined in the supervisor's data dictionary
    note: str = ""
    drug_class: str = ""      # e.g. "analgesic drug (Analgesia)"
    concentration: str = ""   # preparation, e.g. "20 mcg/mL"
    clinical_note: str = ""   # supervisor's note on typical use
    kind_code: str = ""       # CE / CP / CT / RATE / VOL


def drug_info(track_name: str) -> DrugInfo:
    """Interpret a drug-related track name.

    Tracks listed in the supervisor's data dictionary (Orchestra pump tracks) are *verified*.
    Anything else is labelled as unverified and inferred from the name only.
    """
    from . import knowledge as kn
    o = kn.parse_orchestra(track_name)
    if o:
        kind, text = kn.KIND_TEXT[o.kind]
        return DrugInfo(o.drug, kind, True, text, kn.class_label(o.drug_class), o.concentration, o.note, o.kind)
    short = _short(track_name).upper()
    drug = ""
    for key, word in (("PPF", "Propofol"), ("RFTN", "Remifentanil"), ("SEVO", "Sevoflurane"),
                      ("DES", "Desflurane"), ("ISO", "Isoflurane"), ("N2O", "Nitrous oxide"),
                      ("FEN", "Fentanyl"), ("ROC", "Rocuronium"), ("PHE", "Phenylephrine"),
                      ("EPH", "Ephedrine"), ("NE", "Noradrenaline"), ("EPI", "Adrenaline")):
        if key in short:
            drug = word + " (name-based guess)"
            break
    return DrugInfo(drug, DRUG_SEMANTIC_UNKNOWN, False,
                    "Meaning is inferred from the name only. Do not treat as a confirmed administration record.")


# Key signals the overview checks for. Each entry: label -> regex on the full track name.
KEY_SIGNALS: list[tuple[str, str]] = [
    ("Heart rate", r"(^|/)HR$"),
    ("ECG waveform", r"ECG_II|ECG_V5|(^|/)ECG"),
    ("Invasive BP (waveform)", r"(^|/)ART$|ART_WAV"),
    ("Invasive SBP/DBP/MAP", r"ART_(SBP|DBP|MBP)"),
    ("Non-invasive BP", r"NIBP_(SBP|DBP|MBP)"),
    ("SpO2", r"PLETH_SPO2|(^|/)SPO2"),
    ("Plethysmography", r"(^|/)PLETH$|PLETH_HR"),
    ("BIS", r"BIS/BIS$|(^|/)BIS$"),
    ("EEG waveform", r"EEG\d*_WAV|(^|/)EEG"),
    ("ETCO2", r"ETCO2"),
    ("Respiratory rate", r"(^|/)RR$|RR_CO2|(^|/)RESP"),
    ("Propofol infusion (Orchestra)", r"Orchestra/PPF"),
    ("Remifentanil infusion (Orchestra)", r"Orchestra/RFTN"),
    ("Volatile agent (name-based)", r"(^|/|_)(SEVO|DES|ISO)(_|$)"),
]
