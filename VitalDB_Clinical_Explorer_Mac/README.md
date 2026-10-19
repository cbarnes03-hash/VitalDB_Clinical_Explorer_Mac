# VitalDB Clinical Explorer - macOS

A desktop application for exploring the **public VitalDB anaesthesia dataset**: browse cases, inspect
physiological signals, look at drug-delivery tracks alongside physiology, find stat/bolus evidence within a case,
examine the period around a chosen time point, and export data for later statistical or machine-learning work.

> **Research and exploratory data viewer only. Not validated for clinical decision-making. Do not use for
> patient care or drug dosing.**

You do not need to know Python.

The normal workflow is: **extract the ZIP, double-click `install_mac.command` once, then double-click
`run_mac.command`** each time.

---

## 1. Before you start

* **macOS 11 or later** (Apple Silicon or Intel). No administrator rights are needed for the application itself.
* **Python 3.12** from <https://www.python.org/downloads/macos/> (3.10 to 3.13 also work). Download the macOS
  installer and run it once if `python3 --version` in Terminal does not show 3.10 to 3.13.
* **An internet connection** (VitalDB data come from `https://api.vitaldb.net`; opened data are cached locally).
* About 1 GB of free disk space.

The installer creates a private environment in the folder `.venv`. It does **not** change any Python you
already have.

## 2. Installation

1. Double-click the ZIP to extract it (Safari may do this automatically). Move the folder somewhere simple, for
   example your Documents folder.
2. Double-click **`install_mac.command`**. A Terminal window opens. Wait a few minutes until it says **SUCCESS**.
3. **If macOS says the file "cannot be opened because it is from an unidentified developer"** (Gatekeeper):
   Control-click (right-click) the file, choose **Open**, then **Open** again. You only need to do this once per
   file. Alternatively open Terminal, type `cd ` (with a trailing space), drag the extracted folder into the
   Terminal window, press Return, then run `xattr -dr com.apple.quarantine .` and try again.
4. **If a file will not run at all** ("permission denied"), in Terminal run
   `chmod +x install_mac.command run_mac.command` from inside the folder.
5. If the installer says it did not complete, read the message. A detailed log is saved as `install_log.txt`.
   It is safe to run the installer again.

## 3. Starting the application

Double-click **`run_mac.command`**. The application window opens after a few seconds (the Terminal window may
close itself). If the installer has not been run, or something is damaged, the launcher says what to do.

## 4. First use: a five-minute tour

The left-hand menu has these sections. Sections after *Cases* unlock once you open a case.

| Section | What it is for |
|---|---|
| **Cases** | Search the VitalDB catalogue, pick a case, press **Open case** (or double-click a row). You can also type a case ID. |
| **Case overview** | Duration, number of tracks, devices, which key signals are present, which are missing, catalogue metadata. **Check data availability** measures how much of the recording each key numeric signal covers. |
| **Track browser** | Every track in the case, grouped by category. Search by name, select several (Ctrl+click) and press **Add to plot**. **Inspect track** shows its metadata and statistics. |
| **Plot workspace** | Plot several tracks over any interval. Each track gets its own aligned axis. Zoom/pan with the toolbar; hover to read values; save as PNG, PDF or SVG. |
| **Playback** | Replays the case like a bedside monitor: numeric tiles and scrolling traces that follow a playhead. Play/pause (Space), speed from 1x to 120x, reverse, a draggable slider, click-on-trace to jump, -10 s/+10 s (Left/Right = 5 s), jump between recorded events, type a time to go to. Tiles show the latest recorded sample at the playhead and `--` when it is stale; no interpolation and no alarm limits. |
| **Drug explorer** | Finds drug-related tracks by name, explains what each name does and does not imply, and overlays them with HR, blood pressure, BIS, SpO2. |
| **Dose events** | Looks for stat/bolus evidence inside the open case: recorded text events (actual records), and *derived candidates* from infusion-pump volume steps or rate surges (not confirmed administrations). Double-click a row to plot around it; export the list. Also shows whole-case drug totals from the hospital record (no timing). |
| **Event explorer** | Pick a time (click the plot or type it), choose minutes before/after, add a marker and note, then save/export. Shows any events recorded in the case. |
| **Export** | Save selected tracks and an interval to CSV, plus track lists and case metadata. |
| **Dataset guide** | The supervisor's dictionary: cohort summary, every Orchestra drug/infusion track name (drug, concentration, CE/CP/CT/RATE/VOL meaning), whole-case `intraop_*` totals and interpretation notes. |
| **About / limitations** | What the tool can and cannot infer. Button to clear the local cache. |

**Typing times:** all times are *elapsed time since the start of the recording*. You can type `12:30`
(12 min 30 s), `1:02:30`, `90s`, `15 min`, `1h`, or just `12.5` (minutes).

### Getting to a VitalDB case

1. Open **Cases**. The first time, the catalogue (about 6,000 cases) is downloaded; afterwards a cached copy is
   used and the line at the bottom says *"Loaded from local cache saved ..."*. Press **Refresh catalogue** to
   download it again; the line then says *"Freshly retrieved from VitalDB"*.
2. Use the search box (any text, or a chosen column) and the numeric filter (for example age from 40 to 60).
   Click a column heading to sort.
3. Select a case and press **Open case**. Only the list of tracks is fetched at this point (quick). Signal data
   are downloaded when you first plot, inspect or export a track, and cached afterwards. Waveform tracks
   (ECG, arterial pressure, EEG) are large and can take a minute.

## 5. What the data look like (and what the application assumes)

* The public dataset has **different tracks in different cases**. The application never assumes a track exists:
  absent tracks are reported as absent.
* Track names are shown **exactly as VitalDB returns them** (for example `Solar8000/HR`,
  `Orchestra/RFTN20_CE`).
* Units and sampling rates are shown only if the file provides them; otherwise it says *not provided*. Sampling
  rates are the nominal values from the file header.
* **Categories** (haemodynamics, respiratory, drug delivery, ...) are guessed from track names to help you
  navigate. They are not a verified description of what a track measures.
* **Time reference:** elapsed seconds since the first timestamp of the recording. VitalDB de-identifies real
  clock times, so no calendar time is implied.

### Display versus underlying data

* Long tracks are **reduced only for drawing** (min/max decimation: each bin keeps its smallest and largest
  value, so peaks are preserved and nothing is averaged). When you zoom, the plot is redrawn from the original
  samples. A note under the plot states when reduction is in effect.
* **Exports use the original samples**, unless you explicitly choose the regular-grid format.
* Missing values stay missing. Lines are **broken across gaps**; nothing is interpolated or filled. Gaps stored
  by VitalDB as empty (NaN) stretches in waveforms are treated as gaps too.
* Event/text tracks are never averaged or reduced.

### Drug tracks: handle with care

* Drug-related tracks are found **by name only**. They are **not confirmed administration records**.
* Orchestra infusion-pump track names listed in the supervisor's dictionary (Dataset guide) are described
  specifically: drug, concentration and what CE / CP / CT / RATE / VOL mean. Every other drug-like track is
  labelled **UNVERIFIED**.
* A pump-reported concentration is a **model prediction**, not a measured concentration. A *target* is not an
  *effect-site* concentration.
* No unit conversion is done. **Boluses:** only recorded text events (for example "Fentanyl 100 mcg bolus") are
  actual records. Steps found in pump volume or rate are shown as *candidates* (a manual bolus, a TCI loading
  dose and a pump flush can look alike), and `intraop_*` totals have no timing. The *Dose events* page labels
  each kind separately and its sensitivity is adjustable.

### Export files

Every CSV export is accompanied by a `..._manifest.json` file recording the case, tracks, units, interval,
settings, any processing applied, your markers, and the time reference.

* **Original samples (long format):** columns `track, unit, time_s, value`; one row per sample. Different
  sampling rates are preserved exactly. Best for later analysis.
* **Aligned table on a regular grid:** one column per track (original names). The default method takes the
  *nearest original sample within half a step*, otherwise leaves the cell empty. Optionally the mean per bin
  for numeric trend tracks (waveforms always use nearest-sample). The method is written in the manifest.
* Large requests trigger a warning. Existing files are never overwritten without asking.
* Files are saved as UTF-8 (with a marker so Excel opens them correctly).

## 6. What this application can and cannot infer

The application helps you **look at** signals together. It does **not**:

* measure, score or detect **nociception or pain**, or recommend analgesia or dosing;
* treat a remifentanil change as ground truth: it is at most a **potential proxy** for inadequate analgesia;
* show causation: correlation in a plot does not establish that a drug caused a physiological change, and
  drug delivery and physiology are confounded by surgical stimulation, other drugs, fluids, positioning and
  many other factors;
* correct for different sampling rates or missing signals, which can bias later analyses (check missingness;
  the statistics tools help);
* judge signal quality clinically. The *exploratory flags* (empty, constant, sparse, gaps) use **no
  physiological thresholds** and are not clinically validated;
* show anything not in the dataset: no demographics, diagnoses or medications are displayed unless VitalDB
  provides them in its catalogue.

Please follow the VitalDB data-use terms and citation requirements (<https://vitaldb.net>) when publishing.


## Verification status (please read)

* The automated tests (synthetic `.vital` files, offscreen GUI) pass. **The live VitalDB service was not reachable
  from the build environment, so no live download has been tested by the developer.** The first live run on your
  machine is the real test: open a case from *Cases* and report any message that appears.
* The macOS scripts were run on Linux bash with a real install into a folder containing spaces, but they have **not been run on real macOS hardware**. If one fails, `install_log.txt` shows why.

## 7. Troubleshooting

| Problem | What to do |
|---|---|
| Installer says Python was not found | Install Python 3.12 from python.org (macOS installer), then double-click `install_mac.command` again. |
| macOS blocks the script | Control-click the file, choose **Open**, then **Open** (see section 2). |
| Installer fails while downloading packages | Check internet access; a hospital/university firewall or proxy may block `pypi.org` and `files.pythonhosted.org`. Ask IT to allow them, or run the installer on another network. See `install_log.txt`. |
| Launcher says the application is not installed | Run `install_mac.command` first. |
| "Could not reach VitalDB" | Check internet; make sure `https://api.vitaldb.net` is not blocked. If a cache exists, the catalogue is shown from it with a warning. |
| A case "is not available" | Not every case number exists in the public dataset. Pick one from the catalogue. |
| A plot is slow | Waveform tracks are large. Use a shorter interval, fewer waveform tracks, or a lower *Display detail* setting. The first load of a track is the slow part; later loads use the cache. |
| Excel shows a CSV oddly | Use *Data > From Text/CSV* and choose UTF-8, or open in another tool. Empty cells are genuinely missing values. |
| The program will not start | Run `install_mac.command` again. Logs are in `~/Library/Application Support/VitalDB_Clinical_Explorer/app.log`. |
| Free up disk space | *About / limitations > Clear downloaded data cache*. |

Local data (cache, settings, log) are stored in `~/Library/Application Support/VitalDB_Clinical_Explorer`. To remove the
application completely, delete the project folder and that folder.

## 8. Project layout (for developers)

```
install_mac.command      one-time installer (creates .venv, installs requirements, self-check)
run_mac.command          launcher (uses .venv/bin/python only)
check_install.py         import self-check used by both scripts
run_app.py               entry point
requirements.txt         dependencies
vitaldb_explorer/
  config.py              paths, endpoints, limits, small settings store
  data_access.py         catalogue + case reading via the official vitaldb package, caching, cancellation
  knowledge.py           transcribed supervisor spreadsheet (Orchestra dictionary, intraop_* totals, cohort)
  categories.py          name-based categories and cautious drug-track interpretation
  doses.py               stat/bolus evidence: recorded events, derived candidates, whole-case totals
  processing.py          display reduction, gap handling, statistics, aligned/long datasets
  export.py              CSV + manifest + metadata/track-list writers, overwrite protection
  timeutil.py            time parsing/formatting
  app.py                 application start-up
  gui/                   main window, pages, plot panel, background workers (all GUI code)
tests/                   pytest suite (synthetic .vital files; runs offline)
```

Running the tests (optional, from Terminal in the project folder):

```
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests
```

All VitalDB network access is in `data_access.py`. All GUI callbacks from background threads are delivered on
the GUI thread (`gui/workers.py`).
