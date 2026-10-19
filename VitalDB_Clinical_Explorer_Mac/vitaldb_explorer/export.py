"""Exports: CSV data, manifests, metadata, track lists.

Every data export is accompanied by a small JSON manifest that records what
was exported and what processing (if any) was applied, so an analysis can be
reproduced later. Files are never overwritten unless ``overwrite=True`` is
passed explicitly (the GUI asks the user first).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from . import __version__, config
from .data_access import CaseHeader, RawTrack
from .processing import AlignedResult

TIME_REFERENCE = ("time_s = seconds since the start of the VitalDB recording (first timestamp in the "
                  ".vital file). VitalDB clock times are de-identified, so no real calendar time is implied.")


class ExportError(Exception):
    """Export problem with a clinician-safe message."""


def manifest_path_for(csv_path: Path) -> Path:
    return csv_path.with_name(csv_path.stem + "_manifest.json")


def _check_target(path: Path, overwrite: bool) -> None:
    if not path.parent.exists():
        raise ExportError(f"The folder does not exist: {path.parent}")
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    manifest = manifest_path_for(path)
    if path.suffix.lower() == ".csv" and manifest.exists() and not overwrite:
        raise FileExistsError(str(manifest))


def conflicting_files(path: Path) -> list[Path]:
    files = [path]
    if path.suffix.lower() == ".csv":
        files.append(manifest_path_for(path))
    return [p for p in files if p.exists()]


def build_manifest(header: CaseHeader, tracks: list[str], t0: float, t1: float, kind: str,
                   processing: list[str], warnings: list[str], n_rows: int,
                   settings: Optional[dict] = None, markers: Optional[list] = None) -> dict:
    return {
        "application": f"VitalDB Clinical Explorer {__version__}",
        "exported_at_local": datetime.now().isoformat(timespec="seconds"),
        "source": f"VitalDB public dataset ({config.API_URL}), file version {config.DATASET_VERSION}",
        "case_id": header.case_id,
        "export_kind": kind,
        "time_reference": TIME_REFERENCE,
        "window_start_s": t0, "window_end_s": t1,
        "tracks": [{"name": n, "unit": header.tracks[n].unit or None,
                    "type": header.tracks[n].kind,
                    "nominal_sampling_rate_hz": header.tracks[n].srate or None} for n in tracks],
        "processing_applied": processing,
        "warnings": warnings,
        "rows": n_rows,
        "settings": settings or {},
        "user_markers": markers or [],
        "missing_values": "Missing samples are left empty in the CSV; nothing is interpolated or filled.",
        "disclaimer": "Research and exploratory use only. Not validated for clinical decision-making.",
    }


def write_dataset_csv(path: Path, result: AlignedResult, header: CaseHeader, tracks: list[str],
                      t0: float, t1: float, kind: str, overwrite: bool = False,
                      settings: Optional[dict] = None, markers: Optional[list] = None) -> tuple[Path, Path]:
    """Write a dataset CSV and its manifest. Returns (csv_path, manifest_path)."""
    path = Path(path)
    _check_target(path, overwrite)
    try:
        # utf-8-sig so that Excel opens units/special characters correctly
        result.frame.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.10g")
        mpath = manifest_path_for(path)
        man = build_manifest(header, tracks, t0, t1, kind, result.processing, result.warnings,
                             len(result.frame), settings, markers)
        mpath.write_text(json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8")
    except PermissionError as exc:
        raise ExportError("Windows would not let the file be written. Close it if it is open in Excel, "
                          "or choose another folder.") from exc
    except OSError as exc:
        raise ExportError(f"The file could not be saved: {exc}") from exc
    return path, mpath


def track_list_frame(header: CaseHeader, loaded: Optional[dict[str, RawTrack]] = None) -> pd.DataFrame:
    rows = []
    for t in header.tracks.values():
        rows.append({"track_name": t.name, "device": t.device, "short_name": t.short_name,
                     "type": t.kind, "unit": t.unit or "", "nominal_sampling_rate_hz": t.srate or "",
                     "category_(name_based)": t.category})
    return pd.DataFrame(rows)


def write_track_list_csv(path: Path, header: CaseHeader, overwrite: bool = False) -> Path:
    path = Path(path)
    _check_target_simple(path, overwrite)
    try:
        track_list_frame(header).to_csv(path, index=False, encoding="utf-8-sig")
    except OSError as exc:
        raise ExportError(f"The file could not be saved: {exc}") from exc
    return path


def write_metadata_json(path: Path, header: CaseHeader, overwrite: bool = False) -> Path:
    path = Path(path)
    _check_target_simple(path, overwrite)
    doc = {
        "case_id": header.case_id,
        "recording_duration_s": header.duration_s,
        "n_tracks": len(header.tracks),
        "time_reference": TIME_REFERENCE,
        "catalogue_fields": {k: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v))
                             for k, v in header.clinical.items()},
        "tracks": track_list_frame(header).to_dict(orient="records"),
        "source": f"VitalDB public dataset ({config.API_URL})",
    }
    try:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError as exc:
        raise ExportError(f"The file could not be saved: {exc}") from exc
    return path


def _check_target_simple(path: Path, overwrite: bool) -> None:
    if not path.parent.exists():
        raise ExportError(f"The folder does not exist: {path.parent}")
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))


def size_warning(n_rows: int, n_cols: int) -> Optional[str]:
    cells = n_rows * max(1, n_cols)
    if n_rows > config.EXPORT_WARN_ROWS or cells > config.EXPORT_WARN_CELLS:
        mb = cells * 9 / 1e6  # rough bytes per CSV cell
        return (f"This export will contain about {n_rows:,} rows x {n_cols} columns "
                f"(roughly {mb:,.0f} MB). It may take a while and be slow to open in Excel.")
    return None
