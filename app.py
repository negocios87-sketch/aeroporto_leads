#!/usr/bin/env python3
"""
Board Academy — Monitor de TME Comercial
Deploy: Render + GitHub

Variável de ambiente obrigatória:
  PIPEDRIVE_TOKEN = sua_api_key

Rodar local:
  pip install flask requests gunicorn
  PIPEDRIVE_TOKEN=xxx python app.py

Deploy Render (gunicorn.conf.py):
  timeout = 120
  workers = 1
"""

import os, csv, io, json, requests
from datetime import datetime, date, timedelta
from flask import Flask, jsonify

# ── CONFIG ──────────────────────────────────────────────────────────
API_TOKEN         = os.environ.get("PIPEDRIVE_TOKEN", "SEU_TOKEN_AQUI")
FILTER_DEALS      = 1258153
FILTER_ACTIVITIES = 1258382
UTM_FIELD         = "8fb3221ab3d91cddaf51e0a9e1bbcda34fc9d28e"
HORA_INI          = 9
HORA_FIM          = 18

SHEET_COLAB    = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vSvwO3Ag2f2cbkVgR1pJZp6fANQcbualGKlAG50fmOljuEGKZ1gJBbSAjRdO3SomXUEVQOWnTvlfHRd"
    "/pub?gid=1782440078&single=true&output=csv"
)
SHEET_FERIADOS = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vSvwO3Ag2f2cbkVgR1pJZp6fANQcbualGKlAG50fmOljuEGKZ1gJBbSAjRdO3SomXUEVQOWnTvlfHRd"
    "/pub?gid=1010928978&single=true&output=csv"
)

app = Flask(__name__)

# ── CACHE ────────────────────────────────────────────────────────────
_cache: dict = {}

def cached(key, fn, ttl=1800):
    now = datetime.now().timestamp()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    data = fn()
    _cache[key] = {"data": data, "ts": now}
    return data

# ── SHEETS ──────────────────────────────────────────────────────────

def _fetch_sdrs():
    r = requests.get(SHEET_COLAB, timeout=15)
    r.raise_for_status()
    r.encoding = "utf-8"
    sdrs = {}
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) < 4:
            continue
        nome, time_, cargo = row[0].strip(), row[2].strip(), row[3].strip()
        if not nome or nome.lower() == "nome":
            continue
        if "sdr" in cargo.lower():
            sdrs[nome] = {"time": time_, "cargo": cargo}
    return sdrs

def _fetch_feriados():
    r = requests.get(SHEET_FERIADOS, timeout=15)
    r.raise_for_status()
    r.encoding = "utf-8"
    feriados = set()
    for row in csv.reader(io.StringIO(r.text)):
        if not row:
            continue
        raw = row[0].strip()
        if not raw or raw.lower() in ("data", "date"):
            continue
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                feriados.add(datetime.strptime(raw, fmt).date())
                break
            except ValueError:
                pass
    return feriados

def get_sdrs():     return cached("sdrs",     _fetch_sdrs,     1800)
def get_feriados(): return cached("feriados", _fetch_feriados, 86400)

# ── REDES ────────────────────────────────────────────────────────────
REDES = {
    "fbads":     {"label": "Meta",      "icon": "fab fa-facebook-f", "color": "#1877F2", "temp": "frio"},
    "google":    {"label": "Google",    "icon": "fab fa-google",     "color": "#EA4335", "temp": "quente"},
    "googleads": {"label": "Google",    "icon": "fab fa-google",     "color": "#EA4335", "temp": "quente"},
    "lkdnads":   {"label": "LinkedIn",  "icon": "fab fa-linkedin-in","color": "#0A66C2", "temp": "morno"},
    "org":       {"label": "Orgânico",  "icon": "fas fa-leaf",       "color": "#22C55E", "temp": "quente"},
    "workshop":  {"label": "Workshop",  "icon": "fas fa-chalkboard-teacher","color": "#F59E0B", "temp": "morno"},
    "evento":    {"label": "Evento",    "icon": "fas fa-calendar-star","color": "#A855F7","temp": "quente"},
}
REDE_DEFAULT = {"label": "Outros", "icon": "fas fa-circle-question", "color": "#64748B", "temp": "frio"}

def resolver_rede(utm_raw):
    if not utm_raw or not str(utm_raw).strip():
        return REDE_DEFAULT
    return REDES.get(str(utm_raw).strip().lower(), REDE_DEFAULT)

# ── PIPEDRIVE ────────────────────────────────────────────────────────

def buscar_pipelines():
    r = requests.get("https://api.pipedrive.com/v1/pipelines",
                     params={"api_token": API_TOKEN}, timeout=15)
    r.raise_for_status()
    return {p["id"]: p["name"] for p in (r.json().get("data") or [])}

def buscar_deals():
    url, deals, start = "https://api.pipedrive.com/v1/deals", [], 0
    while True:
        r = requests.get(url, params={"filter_id": FILTER_DEALS, "api_token": API_TOKEN,
                                       "limit": 500, "start": start}, timeout=30)
        r.raise_for_status()
        data = r.json()
        deals.extend(data.get("data") or [])
        if not data.get("additional_data", {}).get("pagination", {}).get("more_items_in_collection"):
            break
        start += 500
    return deals

def buscar_atividades():
    url = "https://api.pipedrive.com/api/v2/activities"
    headers = {"x-api-token": API_TOKEN}
    ativs, cursor = [], None
    while True:
        params = {"filter_id": FILTER_ACTIVITIES, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        ativs.extend(data.get("data") or [])
        cursor = data.get("additional_data", {}).get("next_cursor")
        if not cursor:
            break
    return ativs

# ── HELPERS ──────────────────────────────────────────────────────────

def parse_dt(s, utc=False):
    if not s:
        return None
    s = str(s).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt - timedelta(hours=3) if utc else dt
        except ValueError:
            pass
    return None

def fmt_hms(seg):
    if seg is None or seg < 0:
        return "—"
    s = int(seg)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

# ── TME ÚTIL ─────────────────────────────────────────────────────────

def effective_start(dt, feriados):
    d = dt.date()
    h = dt.hour + dt.minute / 60
    is_biz = dt.weekday() < 5 and d not in feriados
    if is_biz and HORA_INI <= h < HORA_FIM:
        return dt
    if is_biz and h < HORA_INI:
        return datetime(d.year, d.month, d.day, HORA_INI, 0, 0)
    candidate = d + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in feriados:
        candidate += timedelta(days=1)
    return datetime(candidate.year, candidate.month, candidate.day, HORA_INI, 0, 0)

def calc_biz_seconds(inicio, fim, feriados):
    if fim <= inicio:
        return 0.0
    total, current, guard = 0.0, inicio, 0
    while current.date() <= fim.date() and guard < 500:
        guard += 1
        d = current.date()
        if current.weekday() >= 5 or d in feriados:
            nxt = d + timedelta(days=1)
            current = datetime(nxt.year, nxt.month, nxt.day, HORA_INI, 0, 0)
            continue
        day_s = datetime(d.year, d.month, d.day, HORA_INI, 0, 0)
        day_e = datetime(d.year, d.month, d.day, HORA_FIM, 0, 0)
        seg_s, seg_e = max(current, day_s), min(fim, day_e)
        if seg_e > seg_s:
            total += (seg_e - seg_s).total_seconds()
        if fim <= day_e:
            break
        nxt = d + timedelta(days=1)
        current = datetime(nxt.year, nxt.month, nxt.day, HORA_INI, 0, 0)
    return total

# ── ENDPOINT ─────────────────────────────────────────────────────────

@app.route("/api/monitor")
def monitor_data():
    agora    = datetime.now()
    hoje     = agora.date()
    sdrs_map = get_sdrs()
    feriados = get_feriados()

    pipelines  = buscar_pipelines()
    deals      = buscar_deals()
    atividades = buscar_atividades()

    mapa_ativ = {}
    for a in atividades:
        did = a.get("deal_id")
        if not did:
            continue
        t = parse_dt(a.get("marked_as_done_time"), utc=True)
        if t is None:
            t = parse_dt(a.get("add_time"), utc=True)
        if t is None:
            dd, dt_ = a.get("due_date"), a.get("due_time")
            if dd and dt_:
                try:
                    t = datetime.strptime(f"{dd} {dt_}", "%Y-%m-%d %H:%M")
                except ValueError:
                    pass
        if t is None:
            continue
        if did not in mapa_ativ or t < mapa_ativ[did]:
            mapa_ativ[did] = t

    sdr_acc = {
        nome: {"time": info["time"], "leads_fechados_tme": [],
               "leads_abertos_iso": [], "total": 0}
        for nome, info in sdrs_map.items()
    }

    leads_aguardando = []

    for d in deals:
        dt_cri = parse_dt(d.get("add_time"), utc=True)
        if dt_cri is None or dt_cri.date() != hoje:
            continue
        deal_id    = d.get("id")
        owner      = d.get("user_id") or {}
        owner_name = owner.get("name", "—") if isinstance(owner, dict) else "—"
        if owner_name not in sdrs_map:
            continue

        pipeline_id = d.get("pipeline_id")
        funil       = pipelines.get(pipeline_id, str(pipeline_id) if pipeline_id else "—")
        rede        = resolver_rede(d.get(UTM_FIELD, ""))
        eff         = effective_start(dt_cri, feriados)
        primeira    = mapa_ativ.get(deal_id)
        tem_lig     = primeira is not None

        acc = sdr_acc.setdefault(owner_name, {
            "time": sdrs_map.get(owner_name, {}).get("time", "—"),
            "leads_fechados_tme": [], "leads_abertos_iso": [], "total": 0
        })
        acc["total"] += 1

        if tem_lig:
            tme_seg = calc_biz_seconds(eff, primeira, feriados)
            acc["leads_fechados_tme"].append(tme_seg)
        else:
            tme_seg = calc_biz_seconds(eff, agora, feriados)
            eff_iso = eff.strftime("%Y-%m-%dT%H:%M:%S")
            acc["leads_abertos_iso"].append(eff_iso)
            leads_aguardando.append({
                "deal_id":         deal_id,
                "sdr_nome":        owner_name,
                "sdr_time":        sdrs_map[owner_name]["time"],
                "titulo":          d.get("title", "—"),
                "funil":           funil,
                "rede_label":      rede["label"],
                "rede_icon":       rede["icon"],
                "rede_color":      rede["color"],
                "hora_criacao":    dt_cri.strftime("%H:%M"),
                "criacao_iso":     dt_cri.strftime("%Y-%m-%dT%H:%M:%S"),
                "effective_start": eff_iso,
                "tme_util_seg":    tme_seg,
                "tme_util":        fmt_hms(tme_seg),
            })

    # Mais recente no topo
    leads_aguardando.sort(key=lambda x: x["criacao_iso"], reverse=True)

    # SDRs
    sdrs_lista = []
    for nome, acc in sdr_acc.items():
        if not sdrs_map.get(nome) or acc["total"] == 0:
            continue
        ab_segs  = [calc_biz_seconds(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S"), agora, feriados)
                    for iso in acc["leads_abertos_iso"]]
        all_tmes = acc["leads_fechados_tme"] + ab_segs
        med_g    = sum(all_tmes) / len(all_tmes) if all_tmes else 0
        sdrs_lista.append({
            "nome":                   nome,
            "time":                   acc["time"],
            "leads_hoje":             acc["total"],
            "leads_abertos_ct":       len(acc["leads_abertos_iso"]),
            "leads_abertos_iso":      acc["leads_abertos_iso"],
            "leads_fechados_tme_sum": sum(acc["leads_fechados_tme"]),
            "leads_fechados_ct":      len(acc["leads_fechados_tme"]),
            "tme_medio_geral":        fmt_hms(med_g),
            "tme_medio_geral_seg":    med_g,
        })
    sdrs_lista.sort(key=lambda x: x["tme_medio_geral_seg"], reverse=True)

    # Times
    times_acc = {}
    for s in sdrs_lista:
        t = s["time"] or "Sem time"
        if t not in times_acc:
            times_acc[t] = {"sdrs": 0, "fech_sum": 0, "fech_ct": 0,
                             "ab_isos": [], "total": 0}
        times_acc[t]["sdrs"]     += 1
        times_acc[t]["fech_sum"] += s["leads_fechados_tme_sum"]
        times_acc[t]["fech_ct"]  += s["leads_fechados_ct"]
        times_acc[t]["ab_isos"].extend(s["leads_abertos_iso"])
        times_acc[t]["total"]    += s["leads_hoje"]

    times_lista = []
    for nome_t, ta in times_acc.items():
        ab_segs  = [calc_biz_seconds(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S"), agora, feriados)
                    for iso in ta["ab_isos"]]
        total_ct = ta["fech_ct"] + len(ab_segs)
        med_g    = (ta["fech_sum"] + sum(ab_segs)) / total_ct if total_ct else 0
        times_lista.append({
            "nome":                   nome_t,
            "sdrs_count":             ta["sdrs"],
            "leads_abertos":          len(ta["ab_isos"]),
            "leads_ab_isos":          ta["ab_isos"],
            "leads_fechados_tme_sum": ta["fech_sum"],
            "leads_fechados_ct":      ta["fech_ct"],
            "leads_hoje":             ta["total"],
            "tme_medio_geral":        fmt_hms(med_g),
            "tme_medio_geral_seg":    med_g,
        })
    times_lista.sort(key=lambda x: x["tme_medio_geral_seg"], reverse=True)

    # KPIs
    total_hoje  = sum(s["leads_hoje"] for s in sdrs_lista)
    aguardando  = len(leads_aguardando)
    atendidos   = total_hoje - aguardando
    all_geral   = []
    for s in sdrs_lista:
        ab_now = [calc_biz_seconds(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S"), agora, feriados)
                  for iso in s["leads_abertos_iso"]]
        fct = s["leads_fechados_ct"]
        all_geral += ([s["leads_fechados_tme_sum"] / fct] * fct if fct else []) + ab_now
    med_geral_global = sum(all_geral) / len(all_geral) if all_geral else None
    tme_ab_now       = [l["tme_util_seg"] for l in leads_aguardando]
    med_ab_global    = sum(tme_ab_now) / len(tme_ab_now) if tme_ab_now else None

    return jsonify({
        "leads_aguardando": leads_aguardando,
        "sdrs":             sdrs_lista,
        "times":            times_lista,
        "kpis": {
            "total":             total_hoje,
            "aguardando":        aguardando,
            "atendidos":         atendidos,
            "tme_medio_geral":   fmt_hms(med_geral_global),
            "tme_medio_abertos": fmt_hms(med_ab_global),
        },
        "feriados":      [f.strftime("%Y-%m-%d") for f in feriados],
        "hora_ini":      HORA_INI,
        "hora_fim":      HORA_FIM,
        "atualizado_em": agora.strftime("%d/%m/%Y %H:%M:%S"),
    })

# ── FRONTEND ─────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Board Academy — Monitor TME</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
:root {
  --bg:      #0B1120;
  --surface: #111827;
  --surf2:   #162032;
  --border:  #1F2D45;
  --gold:    #C9A84C;
  --goldd:   #6B5523;
  --text:    #F1F5F9;
  --ok:      #34D399;
  --ok-bg:   rgba(52,211,153,.07);
  --warn:    #FBBF24;
  --warn-bg: rgba(251,191,36,.07);
  --crit:    #F87171;
  --crit-bg: rgba(248,113,113,.07);
  --muted:   #64748B;
  --mono:    'Space Mono', monospace;
  --sans:    'DM Sans', sans-serif;
}
*, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }
html { font-size:16px; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ══ HEADER ══ */
.header {
  flex-shrink: 0;
  background: #080E1A;
  border-bottom: 1px solid var(--border);
}
.header-main {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  gap: 14px;
}
.brand-name {
  font-family: var(--mono);
  font-size: .9rem;
  font-weight: 700;
  color: var(--gold);
  letter-spacing: 4px;
  text-transform: uppercase;
  white-space: nowrap;
}
.brand-sub {
  font-size: .55rem;
  color: var(--muted);
  letter-spacing: 2px;
  margin-top: 2px;
}
.kpis { display:flex; align-items:center; gap:6px; }
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 5px 14px;
  text-align: center;
  min-width: 80px;
}
.kpi-lbl { font-size:.5rem; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; display:block; margin-bottom:2px; }
.kpi-val { font-family:var(--mono); font-size:1.2rem; font-weight:700; color:var(--gold); }
.kpi.k-red  .kpi-val { color:var(--crit); }
.kpi.k-tme  .kpi-val { color:var(--warn); font-size:.9rem; }

.refresh-info { display:flex; flex-direction:column; align-items:flex-end; gap:3px; }
.refresh-lbl  { font-size:.5rem; color:var(--muted); letter-spacing:1px; }
.refresh-ts   { font-family:var(--mono); font-size:.65rem; color:var(--gold); }
.pbar  { width:110px; height:2px; background:var(--border); border-radius:2px; overflow:hidden; }
.pfill { height:100%; background:var(--gold); border-radius:2px; transition:width 1s linear; }

/* ── TEAM STRIP ── */
.team-strip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 24px;
  border-top: 1px solid var(--border);
  overflow-x: auto;
  flex-wrap: nowrap;
  min-height: 40px;
}
.team-strip::-webkit-scrollbar { height: 2px; }
.team-strip::-webkit-scrollbar-thumb { background: var(--border); }

.t-chip {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
  font-size: .68rem;
  flex-shrink: 0;
  transition: border-color .2s;
}
.t-chip:hover { border-color: #334155; }
.t-chip-nome { font-weight: 700; color: var(--text); }
.t-chip-sep  { color: var(--border); }
.t-chip-val  { font-family: var(--mono); font-size: .65rem; font-weight: 700; }
.t-chip-lbl  { font-size: .55rem; color: var(--muted); }

/* ══ MAIN — DUAS COLUNAS ══ */
.main {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 340px;
  overflow: hidden;
}

/* ── PAINEL ESQUERDO: FILA ── */
.panel-fila {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid var(--border);
}
.sec-hdr {
  flex-shrink: 0;
  height: 32px;
  background: #080E1A;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 20px;
  gap: 8px;
}
.sec-title {
  font-family: var(--mono);
  font-size: .55rem;
  font-weight: 700;
  color: var(--goldd);
  letter-spacing: 3px;
  text-transform: uppercase;
}
.sec-badge {
  background: var(--surf2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 8px;
  font-family: var(--mono);
  font-size: .58rem;
  color: var(--text);
}
.sec-note {
  margin-left: auto;
  font-size: .52rem;
  color: var(--muted);
  letter-spacing: .5px;
}
.sec-line { flex:1; height:1px; background:var(--border); }

.fila-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}
.fila-scroll::-webkit-scrollbar { width: 3px; }
.fila-scroll::-webkit-scrollbar-thumb { background: var(--border); }

/* ── PAINEL DIREITO: SDRs ── */
.panel-sdrs {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.sdrs-scroll {
  flex: 1;
  overflow-y: auto;
}
.sdrs-scroll::-webkit-scrollbar { width: 3px; }
.sdrs-scroll::-webkit-scrollbar-thumb { background: var(--border); }

/* ══ TABELA FILA ══ */
.tbl { width:100%; border-collapse:separate; border-spacing:0 2px; padding:6px 16px; }
.tbl thead th {
  font-family: var(--mono);
  font-size: .52rem;
  font-weight: 700;
  color: var(--goldd);
  letter-spacing: 2px;
  text-transform: uppercase;
  padding: 4px 8px 8px;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg);
  z-index: 10;
}
.tbl tbody tr { background:var(--surface); animation:rowIn .3s ease both; }
.tbl tbody tr.r-ok   { background:var(--ok-bg);   border-left:2px solid var(--ok); }
.tbl tbody tr.r-warn { background:var(--warn-bg); border-left:2px solid var(--warn); }
.tbl tbody tr.r-crit { background:var(--crit-bg); border-left:2px solid var(--crit); }
.tbl tbody tr:hover  { filter: brightness(1.15); }

.tbl tbody td {
  padding: 8px 8px;
  font-size: .78rem;
  color: var(--text);
  border-top: 1px solid #0F1929;
  border-bottom: 1px solid #0F1929;
  white-space: nowrap;
}
.tbl tbody td:first-child { border-radius:4px 0 0 4px; padding-left:10px; }
.tbl tbody td:last-child  { border-radius:0 4px 4px 0; }

.td-id    { font-family:var(--mono); font-size:.65rem; color:#94A3B8; }
.td-sdr   { font-weight:700; color:var(--text); }
.td-time  { font-size:.72rem; color:#94A3B8; }
.td-lead  { font-weight:500; max-width:180px; overflow:hidden; text-overflow:ellipsis; }
.td-funil { font-size:.72rem; color:#94A3B8; max-width:120px; overflow:hidden; text-overflow:ellipsis; }
.td-hora  { font-family:var(--mono); color:var(--gold); font-weight:700; }
.td-tme   { font-family:var(--mono); font-size:.88rem; font-weight:700; }
.ok-val   { color:var(--ok); }
.warn-val { color:var(--warn); }
.crit-val { color:var(--crit); }

.rede-icon { font-size:.9rem; }

/* ══ TABELA SDRs ══ */
.tbl-sdrs { width:100%; border-collapse:separate; border-spacing:0 2px; padding:6px 12px; }
.tbl-sdrs thead th {
  font-family: var(--mono);
  font-size: .46rem;
  font-weight: 700;
  color: var(--goldd);
  letter-spacing: 1.5px;
  text-transform: uppercase;
  padding: 3px 5px 6px;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg);
  z-index: 10;
}
.tbl-sdrs thead th.tr { text-align:right; }
.tbl-sdrs tbody tr { background:var(--surface); animation:rowIn .3s ease both; }
.tbl-sdrs tbody tr:hover { filter:brightness(1.15); }
.tbl-sdrs tbody td {
  padding: 6px 5px;
  font-size: .7rem;
  color: var(--text);
  border-top: 1px solid #0F1929;
  border-bottom: 1px solid #0F1929;
  white-space: nowrap;
}
.tbl-sdrs tbody td:first-child { border-radius:4px 0 0 4px; padding-left:8px; }
.tbl-sdrs tbody td:last-child  { border-radius:0 4px 4px 0; }
.tbl-sdrs td.tr { text-align:right; }

.sdr-nome { font-weight:700; font-size:.72rem; }
.sdr-time { font-size:.62rem; color:#94A3B8; display:block; margin-top:1px; }
.sdr-ct   { font-family:var(--mono); font-weight:700; font-size:.72rem; }
.tme-mono { font-family:var(--mono); font-size:.72rem; font-weight:700; }

.mini-bar  { width:50px; height:3px; background:#1F2937; border-radius:3px; overflow:hidden; display:inline-block; vertical-align:middle; margin-left:5px; }
.mini-fill { height:100%; border-radius:3px; transition:width .5s ease; }

/* ── ANIMAÇÕES ── */
@keyframes rowIn {
  from { opacity:0; transform:translateY(-3px); }
  to   { opacity:1; transform:translateY(0); }
}

/* ── FOOTER ── */
.footer {
  flex-shrink: 0;
  height: 30px;
  background: #080E1A;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  font-size: .55rem;
  color: var(--muted);
  letter-spacing: 1px;
}
.footer-clock { font-family:var(--mono); color:var(--gold); font-size:.7rem; letter-spacing:2px; }

/* ── EMPTY ── */
.empty { display:flex; flex-direction:column; align-items:center; justify-content:center; padding:40px 0; gap:8px; color:var(--muted); }
.empty-icon { font-size:2rem; }
.empty-txt  { font-family:var(--mono); font-size:.65rem; letter-spacing:3px; text-transform:uppercase; }
</style>
</head>
<body>

<!-- ══ HEADER ══ -->
<header class="header">
  <div class="header-main">
    <div>
      <div class="brand-name">Board Academy</div>
      <div class="brand-sub">MONITOR DE TME · SDRs · TEMPO REAL</div>
    </div>
    <div class="kpis">
      <div class="kpi">
        <span class="kpi-lbl">Total Hoje</span>
        <span class="kpi-val" id="k-total">—</span>
      </div>
      <div class="kpi">
        <span class="kpi-lbl">Atendidos</span>
        <span class="kpi-val" id="k-at">—</span>
      </div>
      <div class="kpi k-red">
        <span class="kpi-lbl">Aguardando</span>
        <span class="kpi-val" id="k-ag">—</span>
      </div>
      <div class="kpi k-tme">
        <span class="kpi-lbl">TME Médio Geral</span>
        <span class="kpi-val" id="k-tg">—</span>
      </div>
      <div class="kpi k-tme">
        <span class="kpi-lbl">TME Abertos</span>
        <span class="kpi-val" id="k-ta">—</span>
      </div>
      <div class="refresh-info">
        <span class="refresh-lbl">Atualizado em</span>
        <span class="refresh-ts" id="r-ts">—</span>
        <div class="pbar"><div class="pfill" id="pfill" style="width:0%"></div></div>
      </div>
    </div>
  </div>
  <!-- CHIPS DE TIME -->
  <div class="team-strip" id="team-strip">
    <span style="font-size:.6rem;color:var(--muted);letter-spacing:1px">Carregando times...</span>
  </div>
</header>

<!-- ══ MAIN ══ -->
<div class="main">

  <!-- ESQUERDA: FILA -->
  <div class="panel-fila">
    <div class="sec-hdr">
      <span class="sec-title">Fila Aguardando 1ª Ligação</span>
      <span class="sec-badge" id="fila-ct">—</span>
      <span class="sec-note">▼ mais recente no topo &nbsp;·&nbsp; cores = horário útil 09h–18h</span>
    </div>
    <div class="fila-scroll" id="fila-wrap">
      <div class="empty"><span class="empty-icon">📡</span><span class="empty-txt">Carregando</span></div>
    </div>
  </div>

  <!-- DIREITA: SDRs -->
  <div class="panel-sdrs">
    <div class="sec-hdr">
      <span class="sec-title">Por SDR</span>
      <div class="sec-line"></div>
    </div>
    <div class="sdrs-scroll" id="sdrs-wrap">
      <div class="empty"><span class="empty-txt">—</span></div>
    </div>
  </div>

</div>

<!-- ══ FOOTER ══ -->
<footer class="footer">
  <span>BOARD ACADEMY &nbsp;·&nbsp; Monitor TME &nbsp;·&nbsp; Atualização a cada 60s</span>
  <span>
    <span style="color:var(--ok)">■</span> até 5min &nbsp;
    <span style="color:var(--warn)">■</span> 5–10min &nbsp;
    <span style="color:var(--crit)">■</span> acima de 10min
  </span>
  <span class="footer-clock" id="relogio">00:00:00</span>
</footer>

<script>
// ── ESTADO ──────────────────────────────────────────────────────────
let appData     = null;
let feriadosSet = new Set();
let HORA_INI    = 9, HORA_FIM = 18;
const REFRESH   = 60;
let elapsed = 0, progressTimer = null;

// ── RELÓGIO ─────────────────────────────────────────────────────────
function tickClock() {
  const n = new Date();
  document.getElementById('relogio').textContent =
    [n.getHours(), n.getMinutes(), n.getSeconds()]
      .map(v => String(v).padStart(2,'0')).join(':');
}
setInterval(tickClock, 1000); tickClock();

// ── PROGRESS BAR ────────────────────────────────────────────────────
function startProgress() {
  clearInterval(progressTimer); elapsed = 0;
  document.getElementById('pfill').style.width = '0%';
  progressTimer = setInterval(() => {
    elapsed++;
    document.getElementById('pfill').style.width = Math.min(100,(elapsed/REFRESH)*100) + '%';
    if (elapsed >= REFRESH) clearInterval(progressTimer);
  }, 1000);
}

// ── TME PAREDE (display — sempre toca) ──────────────────────────────
function wallSeconds(iso) {
  return Math.max(0, (Date.now() - new Date(iso)) / 1000);
}

// ── TME ÚTIL (cor e médias) ──────────────────────────────────────────
function bizSeconds(effIso) {
  const start = new Date(effIso), now = new Date();
  if (now <= start) return 0;
  let total = 0, cur = new Date(start), guard = 0;
  while (cur < now && guard++ < 500) {
    const ds  = cur.toISOString().slice(0,10);
    const dow = cur.getDay();
    if (dow === 0 || dow === 6 || feriadosSet.has(ds)) {
      cur = new Date(cur); cur.setDate(cur.getDate()+1); cur.setHours(HORA_INI,0,0,0);
      continue;
    }
    const dS = new Date(cur); dS.setHours(HORA_INI,0,0,0);
    const dE = new Date(cur); dE.setHours(HORA_FIM,0,0,0);
    const sS = cur > dS ? cur : dS;
    const sE = now < dE ? now : dE;
    if (sE > sS) total += (sE - sS) / 1000;
    if (now <= dE) break;
    cur = new Date(cur); cur.setDate(cur.getDate()+1); cur.setHours(HORA_INI,0,0,0);
  }
  return Math.max(0, total);
}

function hms(s) {
  s = Math.max(0, Math.floor(s));
  return `${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor((s%3600)/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
}
function statusOf(s) { return s < 300 ? 'ok' : s < 600 ? 'warn' : 'crit'; }
function barClr(st)  { return st==='ok'?'var(--ok)':st==='warn'?'var(--warn)':'var(--crit)'; }
function eid(n)      { return n.replace(/[^a-zA-Z0-9]/g,'_'); }

// ── TICK AO VIVO ────────────────────────────────────────────────────
function tick() {
  if (!appData) return;

  // Fila: parede para display, útil para cor
  document.querySelectorAll('tr[data-cri]').forEach(tr => {
    const wall = wallSeconds(tr.dataset.cri);
    const biz  = bizSeconds(tr.dataset.eff);
    const st   = statusOf(biz);
    const el   = tr.querySelector('.td-tme');
    if (el) { el.textContent = hms(wall); el.className = 'td-tme ' + st + '-val'; }
    tr.className = 'r-' + st;
  });

  // SDRs: TME abertos ao vivo
  (appData.sdrs || []).forEach(s => {
    const abSegs = (s.leads_abertos_iso || []).map(bizSeconds);
    const medAb  = abSegs.length ? abSegs.reduce((a,b)=>a+b,0)/abSegs.length : null;

    const abEl = document.getElementById('sab-' + eid(s.nome));
    if (abEl) {
      if (medAb !== null) {
        const st = statusOf(medAb);
        abEl.textContent = hms(medAb);
        abEl.className   = 'tme-mono ' + st + '-val';
        const bar = document.getElementById('bar-' + eid(s.nome));
        if (bar) { bar.style.width = Math.min(100,(medAb/600)*100)+'%'; bar.style.background = barClr(st); }
      } else {
        abEl.textContent = '—'; abEl.className = 'tme-mono ok-val';
      }
    }

    // TME geral ao vivo
    const gEl = document.getElementById('sger-' + eid(s.nome));
    if (gEl) {
      const abSum = abSegs.reduce((a,b)=>a+b,0);
      const ct    = (s.leads_fechados_ct||0) + abSegs.length;
      const medG  = ct > 0 ? ((s.leads_fechados_tme_sum||0) + abSum) / ct : null;
      if (medG !== null) {
        const st = statusOf(medG);
        gEl.textContent = hms(medG);
        gEl.className   = 'tme-mono ' + st + '-val';
      } else { gEl.textContent = '—'; gEl.className = 'tme-mono ok-val'; }
    }
  });

  // Times chips ao vivo
  (appData.times || []).forEach(t => {
    const abSegs = (t.leads_ab_isos||[]).map(bizSeconds);
    const abSum  = abSegs.reduce((a,b)=>a+b,0);
    const ct     = (t.leads_fechados_ct||0) + abSegs.length;
    const medG   = ct > 0 ? ((t.leads_fechados_tme_sum||0) + abSum) / ct : null;
    const medAb  = abSegs.length ? abSum/abSegs.length : null;
    const stG    = medG  != null ? statusOf(medG)  : 'ok';
    const stAb   = medAb != null ? statusOf(medAb) : 'ok';
    const gEl    = document.getElementById('tger-' + eid(t.nome));
    const aEl    = document.getElementById('tab-'  + eid(t.nome));
    if (gEl) { gEl.textContent = medG  != null ? hms(medG)  : '—'; gEl.className = 't-chip-val ' + stG  + '-val'; }
    if (aEl) { aEl.textContent = medAb != null ? hms(medAb) : '—'; aEl.className = 't-chip-val ' + stAb + '-val'; }
  });

  // KPI TME abertos ao vivo
  const allAb = (appData.leads_aguardando||[]).map(l => bizSeconds(l.effective_start));
  if (allAb.length > 0) {
    document.getElementById('k-ta').textContent = hms(allAb.reduce((a,b)=>a+b,0)/allAb.length);
  }
}
setInterval(tick, 1000);

// ── RENDER TIMES (chips no header) ──────────────────────────────────
function renderTimes(times) {
  const wrap = document.getElementById('team-strip');
  if (!times || times.length === 0) {
    wrap.innerHTML = '<span style="font-size:.6rem;color:var(--muted)">Sem times</span>';
    return;
  }
  wrap.innerHTML = times.map(t => {
    const abSegs = (t.leads_ab_isos||[]).map(bizSeconds);
    const abSum  = abSegs.reduce((a,b)=>a+b,0);
    const ct     = (t.leads_fechados_ct||0) + abSegs.length;
    const medG   = ct > 0 ? ((t.leads_fechados_tme_sum||0)+abSum)/ct : null;
    const medAb  = abSegs.length ? abSum/abSegs.length : null;
    const stG    = medG  != null ? statusOf(medG)  : 'ok';
    const stAb   = medAb != null ? statusOf(medAb) : 'ok';
    return `
      <div class="t-chip">
        <span class="t-chip-nome">${t.nome}</span>
        <span class="t-chip-sep">·</span>
        <span>
          <span class="t-chip-lbl">Geral </span>
          <span class="t-chip-val ${stG}-val" id="tger-${eid(t.nome)}">${medG!=null?hms(medG):'—'}</span>
        </span>
        <span class="t-chip-sep">·</span>
        <span>
          <span class="t-chip-lbl">Abertos </span>
          <span class="t-chip-val ${stAb}-val" id="tab-${eid(t.nome)}">${medAb!=null?hms(medAb):'—'}</span>
        </span>
        <span class="t-chip-sep">·</span>
        <span class="t-chip-lbl">${t.leads_abertos} aberto${t.leads_abertos!==1?'s':''}</span>
      </div>`;
  }).join('');
}

// ── RENDER FILA ──────────────────────────────────────────────────────
function renderFila(leads) {
  document.getElementById('fila-ct').textContent = leads.length + ' lead' + (leads.length!==1?'s':'');
  const wrap = document.getElementById('fila-wrap');
  if (leads.length === 0) {
    wrap.innerHTML = '<div class="empty"><span class="empty-icon">✅</span><span class="empty-txt">Todos atendidos hoje</span></div>';
    return;
  }
  const rows = leads.map((l,i) => {
    const biz = bizSeconds(l.effective_start);
    const st  = statusOf(biz);
    return `
      <tr class="r-${st}" data-cri="${l.criacao_iso}" data-eff="${l.effective_start}" style="animation-delay:${i*18}ms">
        <td class="td-id">#${l.deal_id}</td>
        <td class="td-sdr">${l.sdr_nome}</td>
        <td class="td-time">${l.sdr_time}</td>
        <td class="td-lead" title="${l.titulo}">${l.titulo}</td>
        <td class="td-funil" title="${l.funil}">${l.funil}</td>
        <td><i class="rede-icon ${l.rede_icon}" style="color:${l.rede_color}" title="${l.rede_label}"></i></td>
        <td class="td-hora">${l.hora_criacao}</td>
        <td><span class="td-tme ${st}-val">${hms(wallSeconds(l.criacao_iso))}</span></td>
      </tr>`;
  }).join('');
  wrap.innerHTML = `
    <table class="tbl">
      <thead><tr>
        <th>ID</th><th>SDR</th><th>Time</th><th>Lead</th>
        <th>Funil</th><th>Rede</th><th>Chegada</th><th>TME ⏱</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── RENDER SDRs ──────────────────────────────────────────────────────
function renderSDRs(sdrs) {
  const wrap = document.getElementById('sdrs-wrap');
  if (!sdrs || sdrs.length === 0) {
    wrap.innerHTML = '<div class="empty"><span class="empty-txt">Sem dados</span></div>';
    return;
  }
  const rows = sdrs.map((s,i) => {
    const abSegs = (s.leads_abertos_iso||[]).map(bizSeconds);
    const medAb  = abSegs.length ? abSegs.reduce((a,b)=>a+b,0)/abSegs.length : null;
    const abSum  = abSegs.reduce((a,b)=>a+b,0);
    const ct     = (s.leads_fechados_ct||0) + abSegs.length;
    const medG   = ct > 0 ? ((s.leads_fechados_tme_sum||0)+abSum)/ct : null;
    const stG    = medG  != null ? statusOf(medG)  : 'ok';
    const stAb   = medAb != null ? statusOf(medAb) : 'ok';
    const pct    = medAb != null ? Math.min(100,(medAb/600)*100) : 0;
    return `
      <tr style="animation-delay:${i*22}ms">
        <td>
          <span class="sdr-nome">${s.nome}</span>
        </td>
        <td class="tr"><span class="sdr-ct">${s.leads_hoje}</span></td>
        <td class="tr"><span class="sdr-ct">${s.leads_abertos_ct}</span></td>
        <td class="tr">
          <span class="tme-mono ${stG}-val" id="sger-${eid(s.nome)}">${medG!=null?hms(medG):'—'}</span>
        </td>
        <td class="tr">
          <span class="tme-mono ${stAb}-val" id="sab-${eid(s.nome)}">${medAb!=null?hms(medAb):'—'}</span>
        </td>
      </tr>`;
  }).join('');
  wrap.innerHTML = `
    <table class="tbl-sdrs">
      <thead><tr>
        <th>SDR</th>
        <th class="tr">Hoje</th>
        <th class="tr">Ab.</th>
        <th class="tr">TME Geral</th>
        <th class="tr">TME Ab.</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── FETCH ────────────────────────────────────────────────────────────
async function fetchDados() {
  try {
    const r = await fetch('/api/monitor');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    appData     = data;
    feriadosSet = new Set(data.feriados || []);
    HORA_INI    = data.hora_ini || 9;
    HORA_FIM    = data.hora_fim || 18;

    document.getElementById('k-total').textContent = data.kpis.total;
    document.getElementById('k-at').textContent    = data.kpis.atendidos;
    document.getElementById('k-ag').textContent    = data.kpis.aguardando;
    document.getElementById('k-tg').textContent    = data.kpis.tme_medio_geral || '—';
    document.getElementById('k-ta').textContent    = data.kpis.tme_medio_abertos || '—';
    document.getElementById('r-ts').textContent    = data.atualizado_em;

    renderTimes(data.times || []);
    renderFila(data.leads_aguardando || []);
    renderSDRs(data.sdrs || []);
    startProgress();
  } catch(err) {
    console.error('Erro:', err);
    document.getElementById('fila-wrap').innerHTML =
      '<div class="empty"><span class="empty-icon">⚠️</span><span class="empty-txt">Erro de conexão</span></div>';
  }
}

fetchDados();
setInterval(fetchDados, REFRESH * 1000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return HTML

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8052))
    print(f"\n📊  Board Academy — Monitor TME · http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
