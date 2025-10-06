from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List
from datetime import datetime, timedelta, timezone
import csv

app = FastAPI(title="Maintenance Email Generator")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------------- TSV parsing ----------------

def parse_uploaded_tsv(file: UploadFile) -> list[tuple[str,str]]:
    if file is None:
        return []
    content = file.file.read()
    if not content:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1251", errors="strict")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="ignore")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            if "CID" in s and "Label" in s:
                s = s.lstrip("#").strip()
                lines.append(s)
            continue
        lines.append(s)

    rows = list(csv.reader(lines, delimiter="\t"))
    cid_idx = None; label_idx = None; header_row = None
    for i in range(min(5, len(rows))):
        low = [c.strip().lower() for c in rows[i]]
        if "cid" in low and "label" in low:
            cid_idx = low.index("cid"); label_idx = low.index("label"); header_row = i; break
    data_rows = rows[header_row+1:] if header_row is not None else rows
    if cid_idx is None: cid_idx = 0
    if label_idx is None: label_idx = 1

    pairs = []
    for r in data_rows:
        if not r: 
            continue
        cid = r[cid_idx].strip() if cid_idx < len(r) else ""
        label = r[label_idx].strip() if label_idx < len(r) else ""
        if not cid:
            continue
        up = cid.upper()
        if up in {"ENABLED","DISABLED","CID","LABEL"}:
            continue
        pairs.append((cid, label))
    return pairs

def collect_pairs(files: List[UploadFile]) -> list[tuple[str,str]]:
    all_pairs = []
    for f in files or []:
        all_pairs.extend(parse_uploaded_tsv(f))
    return all_pairs

def classify_wl_oc_3poc(pairs: list[tuple[str,str]]):
    wl_wlp, oc_list, poc3_list = set(), set(), set()
    for cid, label in pairs:
        up = cid.upper()
        if up.startswith("OC-900001"):
            continue
        if ("WLP-" in up) or ("WL-" in up):
            wl_wlp.add(cid)
            continue
        if up.startswith("3POC"):
            poc3_list.add(f"{cid} ({label})" if label else cid)
            continue
        if up.startswith("OC"):
            oc_list.add(f"{cid} ({label})" if label else cid)
            continue
    return sorted(wl_wlp), sorted(oc_list), sorted(poc3_list)

# ---------------- Time helpers (UTC+0 display) ----------------

def parse_to_utc(date_str: str, time_str: str, utc_offset_str: str):
    try:
        date_str = (date_str or "").strip()
        time_str = (time_str or "").strip()
        if not date_str or not time_str:
            return None
        offs = (utc_offset_str or "").strip().replace("UTC","").strip()
        d, m, y = date_str.split("/")
        if len(y) == 2: y = "20" + y
        naive = datetime.strptime(f"{d}/{m}/{y} {time_str}", "%d/%m/%Y %H:%M")
        try:
            if ":" in offs:
                sign = -1 if offs.startswith("-") else 1
                hh, mm = offs.replace("+","").replace("-","").split(":")
                minutes = sign * (int(hh)*60 + int(mm))
            elif "." in offs:
                sign = -1 if offs.startswith("-") else 1
                val = float(offs.replace("+","").replace("-",""))
                hh = int(val); mm = int(round((val-hh)*60))
                minutes = sign * (hh*60 + mm)
            else:
                minutes = int(float(offs))*60 if offs else 0
        except Exception:
            minutes = 0
        tz_local = timezone(timedelta(minutes=minutes))
        return naive.replace(tzinfo=tz_local).astimezone(timezone.utc)
    except Exception:
        return None

def fmt_date_utc(dt) -> str:
    return dt.strftime("%d/%m/%Y") if dt else ""

def fmt_time_utc(dt) -> str:
    return dt.strftime("%H:%M") if dt else ""

def humanize_minutes(mins: int) -> str:
    h, m = divmod(max(mins or 0,0), 60)
    if h and m: return f"{h}h {m}m"
    if h: return f"{h}h"
    return f"{m}m"

# ---------------- Email builder ----------------

def build_email(jira_ref: str, pop: str, equipment: str, line: str,
                start_date: str, start_time: str,
                end_date: str, end_time: str,
                utc_single: str, override_downtime: str,
                purpose_presets: List[str], purpose_free: str,
                files: List[UploadFile]):
    pairs = collect_pairs(files)
    wl_wlp, oc_list, poc3_list = classify_wl_oc_3poc(pairs)

    start_utc = parse_to_utc(start_date, start_time, utc_single)
    end_utc = parse_to_utc(end_date, end_time, utc_single)
    calc_downtime_mins = None
    if start_utc and end_utc:
        if end_utc < start_utc:
            end_utc += timedelta(days=1)
        calc_downtime_mins = int((end_utc - start_utc).total_seconds() // 60)

    override = (override_downtime or "").strip().lower()
    zero_aliases = {"0","0m","0min","0 minutes","0mins","0h","0 hr","0 hrs"}
    if override in zero_aliases:
        downtime_final = "0"
    elif override:
        downtime_final = override_downtime.strip()
    elif calc_downtime_mins is not None:
        downtime_final = humanize_minutes(calc_downtime_mins)
    else:
        downtime_final = "[specify]"

    start_date_d = fmt_date_utc(start_utc)
    end_date_d = fmt_date_utc(end_utc)
    start_time_utc = fmt_time_utc(start_utc)
    end_time_utc = fmt_time_utc(end_utc)
    subject_dt = ", ".join(x for x in [
        " - ".join(x for x in [start_date_d, end_date_d] if x),
        " - ".join(x for x in [start_time_utc, end_time_utc] if x),
        "UTC+0"
    ] if x).strip(", ")
    # Bracketed subject variables
    subject = f"Planned Network Maintenance – [{(jira_ref or '').strip()}] [{(pop or '').strip()} / {(equipment or '').strip()}] – [{subject_dt}]".strip()

    purposes = []
    for p in purpose_presets or []:
        p = p.strip()
        if p: purposes.append(p)
    if purpose_free and purpose_free.strip():
        purposes.append(purpose_free.strip())
    purpose_block = "; ".join(purposes) if purposes else "[Enter purpose here]"

    blocks = []
    if wl_wlp:
        blocks.append("WL / WLP:\n" + "\n".join(f"  {x}" for x in wl_wlp))
    if oc_list:
        blocks.append("OC:\n" + "\n".join(f"  {x}" for x in oc_list))
    if poc3_list:
        blocks.append("3POC:\n" + "\n".join(f"  {x}" for x in poc3_list))
    impacted_text = "\n\n".join(blocks) if blocks else "(none detected)"

    line_str = (line or "").strip()
    pop_equip_line = f"{(pop or '').strip()} / {(equipment or '').strip()}" + (f" / {line_str}" if line_str else "")

    if downtime_final.lower() in zero_aliases or downtime_final == "0":
        impact_block = "No service interruption is anticipated."
    else:
        impact_block = f"Downtime: {downtime_final}"

    body = f"""Dear Team,

As part of our ongoing efforts to improve the reliability and performance of our network, we will be carrying out planned maintenance as outlined below:

PoP/Devices/LINE:
{pop_equip_line}

Maintenance Window (UTC+0):
Start: {start_date_d} {start_time_utc}
End:   {end_date_d} {end_time_utc}

Purpose of Maintenance:
{purpose_block}

Affected Customers/Services:
{impacted_text}

Expected Impact:
{impact_block}
"""
    calculated_downtime = humanize_minutes(calc_downtime_mins) if calc_downtime_mins is not None else ""
    return subject, body, calculated_downtime

# ---------------- Routes ----------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/preview")
async def api_preview(
    jira_ref: str = Form(""),
    pop: str = Form(""),
    equipment: str = Form(""),
    line: str = Form(""),
    start_date: str = Form(""),
    start_time: str = Form(""),
    end_date: str = Form(""),
    end_time: str = Form(""),
    utc_single: str = Form("+0"),
    override_downtime: str = Form(""),
    purpose_presets: List[str] = Form(None),
    purpose_free: str = Form(""),
    files: List[UploadFile] = File(None),
):
    try:
        subject, body, calc_dt = build_email(
            jira_ref, pop, equipment, line,
            start_date, start_time, end_date, end_time,
            utc_single, override_downtime,
            purpose_presets or [], purpose_free,
            files
        )
        return JSONResponse({"ok": True, "subject": subject, "body": body, "calculated_downtime": calc_dt})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

@app.post("/download.txt")
async def download_txt(subject: str = Form(...), body: str = Form(...)):
    content = subject + "\n\n" + body
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")
