"""Domain knowledge supplied by the supervising consultant anaesthetist.

Source: the "Vital DB database for CB" spreadsheet (a copy ships in ``docs/``). Everything here is transcribed
from that spreadsheet. Where the spreadsheet is silent the fields are left empty and the application says so.
The Orchestra infusion-pump tracks are treated as *verified* because the supervisor's dictionary defines them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

SOURCE_NOTE = "Supervisor's data dictionary (Vital DB database for CB spreadsheet)"

CLASS_KEY = {"Sed": "sedative", "AA": "anaesthetic agent", "MR": "muscle relaxant", "VA": "vasoactive drug",
             "F": "fluid", "Analgesia": "analgesic drug"}

# Whole-case EMR totals from the case catalogue. They carry NO timing information.
# key -> (description, unit, class key, supervisor's note)
EMR_PARAMS: dict[str, tuple[str, str, str, str]] = {
    "intraop_rbc": ("Intraoperative RBC transfusion", "Unit", "F", "treats anaemia / intra-operative blood loss"),
    "intraop_ffp": ("Intraoperative FFP transfusion", "Unit", "F",
                    "fresh frozen plasma; treats coagulopathy and sometimes hypovolaemia"),
    "intraop_crystalloid": ("Intraoperative crystalloid", "mL", "F", "crystalloid, e.g. Hartmann's"),
    "intraop_colloid": ("Intraoperative colloid", "mL", "F",
                        "starch solutions were used until evidence of increased morbidity (AKI); another colloid "
                        "may be human albumin (5% or 20%)"),
    "intraop_ppf": ("Propofol bolus", "mg", "AA",
                    "may be given at induction or with a sudden increase in BIS or haemodynamics (e.g. hypertension)"),
    "intraop_mdz": ("Midazolam", "mg", "Sed",
                    "may be given at induction or with a sudden increase in BIS (anterograde amnesia, to reduce "
                    "the risk of awareness)"),
    "intraop_ftn": ("Fentanyl", "mcg", "Analgesia",
                    "may be given at induction or with any suggestion of increased pain and/or a sudden increase "
                    "in BIS or haemodynamics (e.g. hypertension)"),
    "intraop_rocu": ("Rocuronium", "mg", "MR",
                     "bolus at induction and intra-operatively; sometimes an infusion (infusion is in Orchestra data)"),
    "intraop_vecu": ("Vecuronium", "mg", "MR", "typically a bolus at induction"),
    "intraop_eph": ("Ephedrine", "mg", "VA", "typically tachycardia and hypertension (indirect sympathomimetic)"),
    "intraop_phe": ("Phenylephrine", "mcg", "VA",
                    "typically hypertension with reflex bradycardia (pure alpha-1 agonist)"),
    "intraop_epi": ("Epinephrine", "mcg", "VA", "adrenaline"),
    "intraop_ca": ("Calcium chloride", "mg", "VA",
                   "treats hypocalcaemia (e.g. ionised Ca < 1.0) or follows extensive transfusion; raises "
                   "contractility and BP"),
}

_TCI = ("CE", "CP", "CT", "RATE", "VOL")
_INF = ("RATE", "VOL")
# prefix -> (drug, concentration, class key, supervisor's note, kinds)
ORCHESTRA_DRUGS: dict[str, tuple[str, str, str, str, tuple[str, ...]]] = {
    "AMD": ("Amiodarone", "2 mg/mL", "", "Cardiac surgery, or AF / other tachyarrhythmias", _INF),
    "DEX2": ("Dexmedetomidine", "2 mcg/mL", "Sed/Analgesia", "Sedative AND a very potent analgesic", _INF),
    "DEX4": ("Dexmedetomidine", "4 mcg/mL", "Sed/Analgesia", "Sedative AND a very potent analgesic", _INF),
    "DOBU": ("Dobutamine", "2 mg/mL", "VA", "Cardiac surgery, sometimes renal transplantation", _INF),
    "DOPA": ("Dopamine", "2 mg/mL", "VA", "Typically renal transplantation", _INF),
    "DTZ": ("Diltiazem", "1 mg/mL", "VA", "Treats hypertension", _INF),
    "EPI": ("Epinephrine (adrenaline)", "20 mcg/mL", "VA", "Cardiac surgery, or severe hypotension / anaphylaxis", _INF),
    "FUT": ("Futhan", "0.5 mg/mL", "", "Supervisor unfamiliar; possibly cardiac bypass, dialysis, DIC, pancreatitis", _INF),
    "MRN": ("Milrinone", "200 mcg/mL", "VA", "Cardiac surgery, or right heart failure / pulmonary hypertension", _INF),
    "NEPI": ("Norepinephrine (noradrenaline)", "20 mcg/mL", "VA", "Cardiac surgery, hypotension / vasoplegia", _INF),
    "NPS": ("Nitroprusside (sodium)", "0.2 mg/mL", "VA", "Antihypertensive / vasodilator", _INF),
    "NTG": ("Nitroglycerin (GTN)", "0.2 mg/mL", "VA", "Antihypertensive, vaso/venodilator", _INF),
    "OXY": ("Oxytocin", "0.2 U/mL", "Oxytocic drug", "Obstetric surgery or minor gynaecological procedures", _INF),
    "PGE1": ("Prostaglandin E1", "2 mg/mL", "", "Spreadsheet note refers to obstetric / gynaecological procedures", _INF),
    "PHEN": ("Phenylephrine", "100 mcg/mL", "VA", "Vasoconstrictor, cardiac and non-cardiac surgery", _INF),
    "PPF20": ("Propofol 2%", "20 mg/mL", "AA", "Target-controlled infusion (TCI)", _TCI),
    "RFTN20": ("Remifentanil", "20 mcg/mL", "Analgesia", "Target-controlled infusion (TCI)", _TCI),
    "RFTN50": ("Remifentanil", "50 mcg/mL", "Analgesia", "Target-controlled infusion (TCI)", _TCI),
    "ROC": ("Rocuronium", "2 mg/mL", "MR", "Used where a lax abdomen is required", _INF),
    "VASO": ("Vasopressin", "0.2 U/mL", "VA", "Vasoplegia with high noradrenaline requirement", _INF),
    "VEC": ("Vecuronium", "1 mg/mL", "MR", "Less commonly used; see rocuronium", _INF),
}

KIND_TEXT: dict[str, tuple[str, str]] = {
    "CE": ("Effect-site concentration",
           "Predicted concentration at the site of drug action (brain / effect compartment), calculated by the pump "
           "model, with a time delay (ke0) after changes in plasma concentration. Not a measured level."),
    "CP": ("Plasma concentration",
           "Predicted concentration in the blood (central compartment) from the pump's pharmacokinetic model. "
           "Not a measured level."),
    "CT": ("Target concentration",
           "The concentration (plasma- or effect-site-targeted) chosen by the anaesthetist that the TCI pump works "
           "to reach and maintain. A setting, not a measurement."),
    "RATE": ("Infusion rate",
             "Rate of infusion; for TCI it changes constantly to achieve the target. Units are as given in the file."),
    "VOL": ("Infused volume (cumulative)", "Total volume infused so far (a running counter)."),
}

DEVICES: list[tuple[str, str, str]] = [
    (r"^bis", "BIS Vista", "EEG monitor"),
    (r"^vigileo", "Vigileo", "Cardiac output monitor (arterial waveform)"),
    (r"^ev1000", "EV1000", "Cardiac output monitor (arterial waveform)"),
    (r"^vigilance", "Vigilance II", "Cardiac output monitor (uses PA catheter)"),
    (r"^cardioq", "CardioQ", "Cardiac output monitor (oesophageal doppler)"),
    (r"^invos", "INVOS", "Cerebral oximeter"),
]


def describe_device(device_name: str) -> str:
    for pat, label, desc in DEVICES:
        if re.search(pat, device_name or "", re.I):
            return f"{label}: {desc}"
    return ""


COHORT_COLUMNS = ["General surgery (n = 4,930)", "Thoracic surgery (n = 1,111)", "Gynecology (n = 230)",
                  "Urology (n = 117)", "Total (n = 6,388)"]
COHORT_ROWS: list[tuple[str, list[tuple[str, list[str], str]]]] = [
    ("Surgical approach", [
        ("Open", ["3,104 (63.0%)", "190 (17.1%)", "65 (28.3%)", "6 (5.1%)", "3,368 (52.7%)"], "Open surgery"),
        ("Videoscopic", ["1,691 (34.2%)", "889 (80.0%)", "140 (60.9%)", "34 (29.1%)", "2,701 (42.3%)"],
         "Laparoscopic / thoracoscopic surgery"),
        ("Robotic", ["135 (2.7%)", "32 (2.9%)", "25 (10.9%)", "77 (65.8%)", "269 (4.2%)"], "Robotic surgery")]),
    ("Anaesthesia", [
        ("General", ["4,630 (93.9%)", "1,093 (98.4%)", "203 (88.3%)", "117 (100.0%)", "6,043 (94.6%)"], ""),
        ("Spinal", ["246 (5.0%)", "0 (0.0%)", "27 (11.7%)", "0 (0.0%)", "273 (4.3%)"], ""),
        ("Sedation", ["54 (1.1%)", "18 (1.6%)", "0 (0.0%)", "0 (0.0%)", "72 (1.1%)"], "")]),
    ("Anaesthetics", [
        ("Sevoflurane", ["2,076 (42.1%)", "255 (23.0%)", "172 (74.8%)", "34 (29.1%)", "2,537 (39.7%)"],
         "Volatile anaesthetic agent"),
        ("Desflurane", ["1,012 (20.5%)", "226 (20.3%)", "69 (30.0%)", "106 (90.6%)", "1,383 (21.6%)"],
         "Volatile anaesthetic agent"),
        ("Propofol TCI", ["2,490 (50.5%)", "996 (89.6%)", "5 (2.2%)", "7 (6.0%)", "3,498 (54.8%)"],
         "Intravenous anaesthetic given as target-controlled infusion"),
        ("Remifentanil TCI", ["3,639 (73.8%)", "1,000 (90.0%)", "110 (47.8%)", "86 (73.5%)", "4,835 (75.7%)"],
         "Can be used with volatile or intravenous anaesthetic agents")]),
    ("Device", [
        ("BIS Vista", ["4,282 (86.9%)", "1,004 (90.4%)", "196 (85.2%)", "84 (71.8%)", "5,566 (87.1%)"], "EEG monitor"),
        ("Vigileo", ["85 (1.7%)", "227 (20.4%)", "4 (1.7%)", "32 (27.4%)", "348 (5.4%)"],
         "Cardiac output monitor (arterial waveform)"),
        ("EV1000", ["598 (12.1%)", "1 (0.1%)", "0 (0.0%)", "0 (0.0%)", "599 (9.4%)"],
         "Cardiac output monitor (arterial waveform)"),
        ("Vigilance II", ["63 (1.3%)", "0 (0.0%)", "0 (0.0%)", "0 (0.0%)", "63 (1.0%)"],
         "Cardiac output monitor (uses PA catheter)"),
        ("CardioQ", ["29 (0.6%)", "0 (0.0%)", "0 (0.0%)", "0 (0.0%)", "29 (0.5%)"],
         "Cardiac output monitor (oesophageal doppler)"),
        ("INVOS", ["33 (0.7%)", "0 (0.0%)", "0 (0.0%)", "0 (0.0%)", "33 (0.5%)"], "Cerebral oximeter")]),
]

SUPERVISOR_NOTES = [
    "Anaesthesia: combining General and Spinal is acceptable and standard practice, although it will affect the "
    "level of analgesia and the requirement for remifentanil. Spinal anaesthesia is often used with sedation.",
    "Anaesthetics: the numbers show use of both volatile agents (sevoflurane, desflurane) and an intravenous agent "
    "(propofol), which could make interpretation more difficult.",
    "Open question: whether bolus drugs can be timestamped. Candidates include anaesthetic agents (ketamine, "
    "propofol) and analgesics (fentanyl, sufentanil, morphine, oxycodone, alfentanil, ketamine, paracetamol, NSAIDs).",
    "Dexmedetomidine (a recorded infusion) is a sedative but also a very potent analgesic.",
]

BOLUS_DRUG_WORDS = ["propofol", "ketamine", "fentanyl", "sufentanil", "morphine", "oxycodone", "alfentanil",
                    "remifentanil", "paracetamol", "acetaminophen", "nsaid", "ketorolac", "ibuprofen", "diclofenac",
                    "midazolam", "rocuronium", "vecuronium", "ephedrine", "phenylephrine", "epinephrine",
                    "adrenaline", "calcium", "atropine", "bolus"]


@dataclass(frozen=True)
class OrchestraTrack:
    prefix: str
    kind: str
    drug: str
    concentration: str
    drug_class: str
    note: str


_ORCH_RX = re.compile(r"^Orchestra/([A-Za-z0-9]+)_(CE|CP|CT|RATE|VOL)$")


def parse_orchestra(track_name: str) -> Optional[OrchestraTrack]:
    m = _ORCH_RX.match(track_name or "")
    if not m:
        return None
    prefix, kind = m.group(1).upper(), m.group(2)
    entry = ORCHESTRA_DRUGS.get(prefix)
    if not entry or kind not in entry[4]:
        return None
    return OrchestraTrack(prefix, kind, entry[0], entry[1], entry[2], entry[3])


def all_orchestra_names() -> list[str]:
    return [f"Orchestra/{p}_{k}" for p, e in ORCHESTRA_DRUGS.items() for k in e[4]]


def class_label(key: str) -> str:
    if not key:
        return "not classified in spreadsheet"
    return " / ".join(f"{CLASS_KEY[p.strip()]} ({p.strip()})" if p.strip() in CLASS_KEY else p.strip()
                      for p in key.split("/"))
