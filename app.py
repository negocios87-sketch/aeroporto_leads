#!/usr/bin/env python3
"""
Board Academy — Monitor de TME Comercial
Deploy: Render + GitHub

Variável de ambiente obrigatória:
  PIPEDRIVE_TOKEN = sua_api_key

Rodar local:
  pip install flask requests gunicorn
  PIPEDRIVE_TOKEN=xxx python app.py

Deploy Render:
  Start command: gunicorn app:app
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

def cached(key: str, fn, ttl: int = 1800):
    now = datetime.now().timestamp()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    data = fn()
    _cache[key] = {"data": data, "ts": now}
    return data

# ── SHEETS ──────────────────────────────────────────────────────────

def _fetch_sdrs() -> dict:
    """Retorna {nome: {time, cargo}} — apenas SDRs."""
    r = requests.get(SHEET_COLAB, timeout=15)
    r.raise_for_status()
    r.encoding = "utf-8"
    sdrs = {}
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) < 4:
            continue
        nome  = row[0].strip()
        time_ = row[2].strip()   # col C = Subárea
        cargo = row[3].strip()   # col D = Cargo
        if not nome or nome.lower() == "nome":
            continue
        if "sdr" in cargo.lower():
            sdrs[nome] = {"time": time_, "cargo": cargo}
    return sdrs

def _fetch_feriados() -> set:
    """Retorna set de date com todos os feriados."""
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

def get_sdrs()     -> dict: return cached("sdrs",     _fetch_sdrs,     1800)
def get_feriados() -> set:  return cached("feriados", _fetch_feriados, 86400)

# ── REDES ────────────────────────────────────────────────────────────
REDES = {
    "fbads":     {"label": "Meta",      "temp": "frio",   "temp_label": "Frio"},
    "google":    {"label": "Google",    "temp": "quente", "temp_label": "Quente"},
    "googleads": {"label": "Google",    "temp": "quente", "temp_label": "Quente"},
    "lkdnads":   {"label": "LinkedIn",  "temp": "morno",  "temp_label": "Morno"},
    "org":       {"label": "Orgânico",  "temp": "quente", "temp_label": "Quente"},
    "workshop":  {"label": "Workshop",  "temp": "morno",  "temp_label": "Morno"},
    "evento":    {"label": "Evento",    "temp": "quente", "temp_label": "Quente"},
}
REDE_DEFAULT = {"label": "Outros", "temp": "frio", "temp_label": "Frio"}

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

# ── CÁLCULO TME ÚTIL ─────────────────────────────────────────────────

def effective_start(dt: datetime, feriados: set) -> datetime:
    """
    Determina o início efetivo do TME conforme as regras:
    I.   Dia útil, 09h–18h → conta da entrada
    II.  Dia útil antes das 09h → conta das 09h do mesmo dia
    III. Dia útil após 18h, feriado, sáb, dom → próximo dia útil às 09h
    """
    d = dt.date()
    h = dt.hour + dt.minute / 60
    is_biz = dt.weekday() < 5 and d not in feriados

    if is_biz and HORA_INI <= h < HORA_FIM:
        return dt                                                      # Caso I
    if is_biz and h < HORA_INI:
        return datetime(d.year, d.month, d.day, HORA_INI, 0, 0)      # Caso II

    # Caso III: avança para o próximo dia útil
    candidate = d + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in feriados:
        candidate += timedelta(days=1)
    return datetime(candidate.year, candidate.month, candidate.day, HORA_INI, 0, 0)

def calc_biz_seconds(inicio: datetime, fim: datetime, feriados: set) -> float:
    """Segundos em horário útil entre dois datetimes."""
    if fim <= inicio:
        return 0.0
    total = 0.0
    current = inicio
    guard = 0
    while current.date() <= fim.date() and guard < 500:
        guard += 1
        d = current.date()
        if current.weekday() >= 5 or d in feriados:
            nxt = d + timedelta(days=1)
            current = datetime(nxt.year, nxt.month, nxt.day, HORA_INI, 0, 0)
            continue
        day_s = datetime(d.year, d.month, d.day, HORA_INI, 0, 0)
        day_e = datetime(d.year, d.month, d.day, HORA_FIM, 0, 0)
        seg_s = max(current, day_s)
        seg_e = min(fim, day_e)
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

    # Mapa deal_id → dt da 1ª atividade do filtro
    mapa_ativ: dict = {}
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

    # Acumuladores por SDR
    # {nome: {time, leads_fechados_tme:[], leads_abertos_iso:[], total:int}}
    sdr_acc: dict = {
        nome: {"time": info["time"], "leads_fechados_tme": [], "leads_abertos_iso": [], "total": 0}
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
            continue  # filtra apenas SDRs

        pipeline_id = d.get("pipeline_id")
        funil       = pipelines.get(pipeline_id, str(pipeline_id) if pipeline_id else "—")
        rede        = resolver_rede(d.get(UTM_FIELD, ""))
        eff         = effective_start(dt_cri, feriados)
        primeira    = mapa_ativ.get(deal_id)
        tem_lig     = primeira is not None

        acc = sdr_acc.setdefault(owner_name, {"time": sdrs_map.get(owner_name, {}).get("time","—"),
                                               "leads_fechados_tme": [], "leads_abertos_iso": [], "total": 0})
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
                "rede_temp":       rede["temp"],
                "rede_temp_label": rede["temp_label"],
                "hora_criacao":    dt_cri.strftime("%H:%M"),
                "effective_start": eff_iso,
                "tme_util_seg":    tme_seg,
                "tme_util":        fmt_hms(tme_seg),
            })

    # Mais urgente no topo
    leads_aguardando.sort(key=lambda x: x["tme_util_seg"], reverse=True)

    # ── Tabela de SDRs ─────────────────────────────────────────────
    sdrs_lista = []
    for nome, acc in sdr_acc.items():
        if not sdrs_map.get(nome):
            continue
        fech_sum  = sum(acc["leads_fechados_tme"])
        fech_ct   = len(acc["leads_fechados_tme"])
        ab_ct     = len(acc["leads_abertos_iso"])
        total     = acc["total"]

        # TME médio geral aproximado (open leads calculados até agora)
        tme_open_now = [calc_biz_seconds(
            datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S"), agora, feriados
        ) for iso in acc["leads_abertos_iso"]]
        all_tmes  = acc["leads_fechados_tme"] + tme_open_now
        med_geral = sum(all_tmes) / len(all_tmes) if all_tmes else 0

        sdrs_lista.append({
            "nome":                  nome,
            "time":                  acc["time"],
            "leads_hoje":            total,
            "leads_abertos_ct":      ab_ct,
            "leads_abertos_iso":     acc["leads_abertos_iso"],
            "leads_fechados_tme_sum": fech_sum,
            "leads_fechados_ct":     fech_ct,
            "tme_medio_geral":       fmt_hms(med_geral),
            "tme_medio_geral_seg":   med_geral,
        })

    sdrs_lista.sort(key=lambda x: x["tme_medio_geral_seg"], reverse=True)

    # ── Médias por Time ────────────────────────────────────────────
    times_acc: dict = {}
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
        ab_tmes   = [calc_biz_seconds(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S"), agora, feriados)
                     for iso in ta["ab_isos"]]
        all_tmes  = ([ta["fech_sum"] / ta["fech_ct"]] * ta["fech_ct"] if ta["fech_ct"] else []) + ab_tmes
        med_geral = (ta["fech_sum"] + sum(ab_tmes)) / ta["total"] if ta["total"] else 0
        times_lista.append({
            "nome":                  nome_t,
            "sdrs_count":            ta["sdrs"],
            "leads_abertos":         len(ta["ab_isos"]),
            "leads_ab_isos":         ta["ab_isos"],
            "leads_fechados_tme_sum": ta["fech_sum"],
            "leads_fechados_ct":     ta["fech_ct"],
            "leads_hoje":            ta["total"],
            "tme_medio_geral":       fmt_hms(med_geral),
            "tme_medio_geral_seg":   med_geral,
        })
    times_lista.sort(key=lambda x: x["tme_medio_geral_seg"], reverse=True)

    # ── KPIs globais ───────────────────────────────────────────────
    total_hoje    = sum(s["leads_hoje"] for s in sdrs_lista)
    aguardando    = len(leads_aguardando)
    atendidos     = total_hoje - aguardando
    all_geral     = []
    for s in sdrs_lista:
        ab_now = [calc_biz_seconds(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S"), agora, feriados)
                  for iso in s["leads_abertos_iso"]]
        all_geral += ([s["leads_fechados_tme_sum"] / s["leads_fechados_ct"]] * s["leads_fechados_ct"]
                       if s["leads_fechados_ct"] else []) + ab_now
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
        "feriados":         [f.strftime("%Y-%m-%d") for f in feriados],
        "hora_ini":         HORA_INI,
        "hora_fim":         HORA_FIM,
        "atualizado_em":    agora.strftime("%d/%m/%Y %H:%M:%S"),
    })

# ── FRONTEND ─────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Board Academy — Monitor TME</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg:       #0B1120;
  --surface:  #111827;
  --surface2: #162032;
  --border:   #1F2D45;
  --gold:     #C9A84C;
  --gold-dim: #6B5523;
  --text:     #F1F5F9;
  --muted:    #64748B;
  --ok:       #34D399;
  --ok-bg:    rgba(52,211,153,.08);
  --warn:     #FBBF24;
  --warn-bg:  rgba(251,191,36,.08);
  --crit:     #F87171;
  --crit-bg:  rgba(248,113,113,.08);
  --mono:     'Space Mono', monospace;
  --sans:     'DM Sans', sans-serif;
}
*, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }
html { font-size: 16px; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ── HEADER ── */
.header {
  flex-shrink: 0;
  height: 64px;
  background: #080E1A;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  gap: 16px;
}
.brand {
  display: flex;
  flex-direction: column;
}
.brand-name {
  font-family: var(--mono);
  font-size: .9rem;
  font-weight: 700;
  color: var(--gold);
  letter-spacing: 4px;
  text-transform: uppercase;
}
.brand-sub {
  font-size: .6rem;
  color: var(--muted);
  letter-spacing: 2px;
  margin-top: 2px;
}
.kpis {
  display: flex;
  align-items: center;
  gap: 8px;
}
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 16px;
  text-align: center;
  min-width: 90px;
}
.kpi-lbl {
  font-size: .52rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  display: block;
  margin-bottom: 2px;
}
.kpi-val {
  font-family: var(--mono);
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--gold);
}
.kpi.k-red  .kpi-val { color: var(--crit); }
.kpi.k-tme  .kpi-val { color: var(--warn); font-size: 1rem; }

.refresh-info {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
}
.refresh-lbl { font-size: .52rem; color: var(--muted); letter-spacing: 1px; }
.refresh-ts  { font-family: var(--mono); font-size: .7rem; color: var(--gold); }
.pbar { width: 120px; height: 2px; background: var(--border); border-radius: 2px; overflow: hidden; }
.pfill { height: 100%; background: var(--gold); border-radius: 2px; transition: width 1s linear; }

/* ── MAIN ── */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── SECTION HEADER ── */
.sec-hdr {
  flex-shrink: 0;
  height: 34px;
  background: #080E1A;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 28px;
  gap: 10px;
}
.sec-hdr-title {
  font-family: var(--mono);
  font-size: .58rem;
  font-weight: 700;
  color: var(--gold-dim);
  letter-spacing: 3px;
  text-transform: uppercase;
}
.sec-hdr-badge {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 8px;
  font-family: var(--mono);
  font-size: .6rem;
  color: var(--muted);
}
.sec-line {
  flex: 1;
  height: 1px;
  background: var(--border);
}

/* ── FILA AGUARDANDO ── */
.section-fila {
  flex: 0 0 42vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.fila-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}
.fila-scroll::-webkit-scrollbar { width: 4px; }
.fila-scroll::-webkit-scrollbar-track { background: var(--bg); }
.fila-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

/* ── TABELA FILA ── */
.tbl { width: 100%; border-collapse: separate; border-spacing: 0 2px; padding: 8px 28px; }
.tbl thead th {
  font-family: var(--mono);
  font-size: .55rem;
  font-weight: 700;
  color: var(--gold-dim);
  letter-spacing: 2.5px;
  text-transform: uppercase;
  padding: 4px 10px 8px;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg);
  z-index: 10;
}
.tbl tbody tr {
  background: var(--surface);
  animation: rowIn .3s ease both;
}
@keyframes rowIn {
  from { opacity:0; transform:translateY(-4px); }
  to   { opacity:1; transform:translateY(0); }
}
.tbl tbody tr:hover { background: var(--surface2); }
.tbl tbody tr.r-ok   { background: var(--ok-bg);   border-left: 2px solid var(--ok); }
.tbl tbody tr.r-warn { background: var(--warn-bg);  border-left: 2px solid var(--warn); }
.tbl tbody tr.r-crit { background: var(--crit-bg);  border-left: 2px solid var(--crit); }
.tbl tbody td {
  padding: 9px 10px;
  font-size: .8rem;
  color: #CBD5E1;
  border-top: 1px solid #0F1929;
  border-bottom: 1px solid #0F1929;
  white-space: nowrap;
}
.tbl tbody td:first-child { border-radius: 4px 0 0 4px; padding-left: 14px; }
.tbl tbody td:last-child  { border-radius: 0 4px 4px 0; }

.td-sdr    { color: var(--text) !important; font-weight: 600; }
.td-titulo { color: var(--text) !important; font-weight: 500; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
.td-funil  { color: var(--muted) !important; font-size: .72rem; max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
.td-hora   { font-family: var(--mono); color: var(--gold) !important; font-weight: 700; }
.td-tme    { font-family: var(--mono); font-size: .95rem; font-weight: 700; }
.ok-val    { color: var(--ok); }
.warn-val  { color: var(--warn); }
.crit-val  { color: var(--crit); }

.temp-badge {
  display: inline-block;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: .65rem;
  font-weight: 600;
  border: 1px solid transparent;
}
.temp-quente { background: rgba(251,113,133,.12); border-color: rgba(251,113,133,.3); color: #FB7185; }
.temp-morno  { background: rgba(251,191,36,.10);  border-color: rgba(251,191,36,.3);  color: var(--warn); }
.temp-frio   { background: rgba(96,165,250,.10);  border-color: rgba(96,165,250,.3);  color: #93C5FD; }

.rede-lbl { font-size: .7rem; color: var(--muted); }

/* ── PERFORMANCE ── */
.section-perf {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* PAINEL TIMES */
.times-panel {
  flex: 0 0 280px;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.times-panel .sec-hdr { border-bottom: 1px solid var(--border); }
.times-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.times-scroll::-webkit-scrollbar { width: 3px; }
.times-scroll::-webkit-scrollbar-thumb { background: var(--border); }

.time-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  animation: rowIn .35s ease both;
}
.time-card-hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.time-card-nome {
  font-size: .82rem;
  font-weight: 700;
  color: var(--text);
}
.time-card-sdrs {
  font-size: .6rem;
  color: var(--muted);
  font-family: var(--mono);
}
.time-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.time-metric {
  background: #080E1A;
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 5px 8px;
}
.time-metric-lbl {
  font-size: .52rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  display: block;
  margin-bottom: 2px;
}
.time-metric-val {
  font-family: var(--mono);
  font-size: .78rem;
  font-weight: 700;
}

/* PAINEL SDRs */
.sdrs-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.sdrs-scroll {
  flex: 1;
  overflow-y: auto;
}
.sdrs-scroll::-webkit-scrollbar { width: 4px; }
.sdrs-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

/* TABELA SDRs */
.tbl-sdrs { width: 100%; border-collapse: separate; border-spacing: 0 2px; padding: 8px 20px; }
.tbl-sdrs thead th {
  font-family: var(--mono);
  font-size: .55rem;
  font-weight: 700;
  color: var(--gold-dim);
  letter-spacing: 2px;
  text-transform: uppercase;
  padding: 4px 10px 8px;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg);
  z-index: 10;
}
.tbl-sdrs thead th.th-r { text-align: right; }
.tbl-sdrs tbody tr { background: var(--surface); animation: rowIn .3s ease both; }
.tbl-sdrs tbody tr:hover { background: var(--surface2); }
.tbl-sdrs tbody td {
  padding: 9px 10px;
  font-size: .8rem;
  color: #CBD5E1;
  border-top: 1px solid #0F1929;
  border-bottom: 1px solid #0F1929;
  white-space: nowrap;
}
.tbl-sdrs tbody td:first-child { border-radius: 4px 0 0 4px; padding-left: 14px; }
.tbl-sdrs tbody td:last-child  { border-radius: 0 4px 4px 0; }
.tbl-sdrs td.td-r { text-align: right; }

.sdr-nome { color: var(--text) !important; font-weight: 600; font-size: .85rem; }
.sdr-time { color: var(--muted) !important; font-size: .7rem; }
.sdr-ct   { font-family: var(--mono); color: var(--text) !important; font-weight: 600; }
.sdr-ct.zero { color: var(--muted) !important; }
.tme-mono { font-family: var(--mono); font-size: .82rem; font-weight: 700; }

/* barra mini no SDR */
.mini-bar { width: 80px; height: 4px; background: #1F2937; border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 6px; }
.mini-fill { height: 100%; border-radius: 3px; transition: width .5s ease; }

/* ── FOOTER ── */
.footer {
  flex-shrink: 0;
  height: 32px;
  background: #080E1A;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  font-size: .58rem;
  color: var(--muted);
  letter-spacing: 1px;
}
.footer-clock { font-family: var(--mono); color: var(--gold); font-size: .72rem; letter-spacing: 2px; }

/* ── EMPTY STATE ── */
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
  gap: 10px;
  color: var(--muted);
}
.empty-icon { font-size: 2.4rem; }
.empty-txt  { font-family: var(--mono); font-size: .72rem; letter-spacing: 3px; text-transform: uppercase; }
</style>
</head>
<body>

<!-- HEADER -->
<header class="header">
  <div class="brand">
    <span class="brand-name">Board Academy</span>
    <span class="brand-sub">MONITOR DE TME COMERCIAL · SDRs · TEMPO REAL</span>
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
</header>

<!-- MAIN -->
<div class="main">

  <!-- FILA -->
  <div class="section-fila">
    <div class="sec-hdr">
      <span class="sec-hdr-title">Fila Aguardando 1ª Ligação</span>
      <span class="sec-hdr-badge" id="fila-ct">0 leads</span>
      <div class="sec-line"></div>
      <span style="font-size:.55rem;color:var(--muted);letter-spacing:1px">▲ mais urgente no topo &nbsp;·&nbsp; horário útil 09h–18h &nbsp;·&nbsp; excl. fins de semana e feriados</span>
    </div>
    <div class="fila-scroll" id="fila-wrap">
      <div class="empty"><span class="empty-icon">📡</span><span class="empty-txt">Carregando</span></div>
    </div>
  </div>

  <!-- PERFORMANCE -->
  <div class="section-perf">

    <!-- TIMES -->
    <div class="times-panel">
      <div class="sec-hdr">
        <span class="sec-hdr-title">Por Time</span>
        <div class="sec-line"></div>
      </div>
      <div class="times-scroll" id="times-wrap">
        <div class="empty"><span class="empty-txt">—</span></div>
      </div>
    </div>

    <!-- SDRs -->
    <div class="sdrs-panel">
      <div class="sec-hdr">
        <span class="sec-hdr-title">Performance por SDR</span>
        <div class="sec-line"></div>
        <span style="font-size:.55rem;color:var(--muted);letter-spacing:1px">ordenado pelo maior TME geral</span>
      </div>
      <div class="sdrs-scroll" id="sdrs-wrap">
        <div class="empty"><span class="empty-txt">—</span></div>
      </div>
    </div>

  </div>
</div>

<!-- FOOTER -->
<footer class="footer">
  <span>BOARD ACADEMY &nbsp;·&nbsp; Monitor de TME &nbsp;·&nbsp; Atualização automática a cada 60s</span>
  <span>Legenda TME: <span style="color:var(--ok)">■</span> até 5min &nbsp; <span style="color:var(--warn)">■</span> 5–10min &nbsp; <span style="color:var(--crit)">■</span> acima de 10min</span>
  <span class="footer-clock" id="relogio">00:00:00</span>
</footer>

<script>
// ── ESTADO GLOBAL ──────────────────────────────────────────────────
let appData       = null;
let feriadosSet   = new Set();
let HORA_INI      = 9;
let HORA_FIM      = 18;
const REFRESH     = 60;
let elapsed       = 0;
let progressTimer = null;

// ── RELÓGIO ────────────────────────────────────────────────────────
function tickClock() {
  const n = new Date();
  document.getElementById('relogio').textContent =
    [n.getHours(), n.getMinutes(), n.getSeconds()]
      .map(v => String(v).padStart(2,'0')).join(':');
}
setInterval(tickClock, 1000);
tickClock();

// ── PROGRESS BAR ──────────────────────────────────────────────────
function startProgress() {
  clearInterval(progressTimer);
  elapsed = 0;
  document.getElementById('pfill').style.width = '0%';
  progressTimer = setInterval(() => {
    elapsed++;
    document.getElementById('pfill').style.width = Math.min(100, (elapsed/REFRESH)*100) + '%';
    if (elapsed >= REFRESH) clearInterval(progressTimer);
  }, 1000);
}

// ── TME ÚTIL (JS) ─────────────────────────────────────────────────
function calcBizSeconds(effStartIso) {
  const start = new Date(effStartIso);
  const now   = new Date();
  if (now <= start) return 0;

  let total = 0;
  let cur   = new Date(start);
  let guard = 0;

  while (cur < now && guard++ < 500) {
    const dayStr = cur.toISOString().slice(0,10);
    const dow    = cur.getDay(); // 0=Dom, 6=Sab
    if (dow === 0 || dow === 6 || feriadosSet.has(dayStr)) {
      cur = new Date(cur);
      cur.setDate(cur.getDate() + 1);
      cur.setHours(HORA_INI, 0, 0, 0);
      continue;
    }
    const dayS = new Date(cur); dayS.setHours(HORA_INI, 0, 0, 0);
    const dayE = new Date(cur); dayE.setHours(HORA_FIM, 0, 0, 0);
    const segS = cur > dayS ? cur : dayS;
    const segE = now < dayE ? now : dayE;
    if (segE > segS) total += (segE - segS) / 1000;
    if (now <= dayE) break;
    cur = new Date(cur);
    cur.setDate(cur.getDate() + 1);
    cur.setHours(HORA_INI, 0, 0, 0);
  }
  return Math.max(0, total);
}

function fmtHMS(s) {
  s = Math.max(0, Math.floor(s));
  return `${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor((s%3600)/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
}

function statusOf(seg) {
  if (seg < 300) return 'ok';
  if (seg < 600) return 'warn';
  return 'crit';
}
function statusClass(seg)   { return 'r-' + statusOf(seg); }
function statusVal(seg)     { return statusOf(seg) + '-val'; }
function barColor(st) {
  return st === 'ok' ? 'var(--ok)' : st === 'warn' ? 'var(--warn)' : 'var(--crit)';
}
function barPct(seg) { return Math.min(100, (seg / 600) * 100); } // 10min = 100%

// ── TICK AO VIVO ──────────────────────────────────────────────────
function tick() {
  if (!appData) return;

  // --- FILA: atualiza TME de cada linha ---
  document.querySelectorAll('tr[data-eff]').forEach(tr => {
    const seg  = calcBizSeconds(tr.dataset.eff);
    const txt  = fmtHMS(seg);
    const st   = statusOf(seg);
    const tmeEl = tr.querySelector('.td-tme');
    if (tmeEl) { tmeEl.textContent = txt; tmeEl.className = 'td-tme ' + st + '-val'; }
    tr.className = 'r-' + st;
  });

  // --- SDRs: TME médio abertos ao vivo ---
  (appData.sdrs || []).forEach(s => {
    const id   = 'sdr-ab-' + encodeId(s.nome);
    const el   = document.getElementById(id);
    if (!el) return;
    if (!s.leads_abertos_iso || s.leads_abertos_iso.length === 0) {
      el.textContent = '—';
      el.className = 'tme-mono ok-val';
      return;
    }
    const segs = s.leads_abertos_iso.map(calcBizSeconds);
    const med  = segs.reduce((a,b)=>a+b,0) / segs.length;
    const st   = statusOf(med);
    el.textContent = fmtHMS(med);
    el.className   = 'tme-mono ' + st + '-val';

    // barra
    const bar = document.getElementById('bar-ab-' + encodeId(s.nome));
    if (bar) {
      bar.style.width      = barPct(med) + '%';
      bar.style.background = barColor(st);
    }

    // TME geral ao vivo
    const gelEl = document.getElementById('sdr-ger-' + encodeId(s.nome));
    if (gelEl) {
      const abSum  = segs.reduce((a,b)=>a+b,0);
      const total  = (s.leads_fechados_tme_sum || 0) + abSum;
      const ct     = (s.leads_fechados_ct || 0) + segs.length;
      const medG   = ct > 0 ? total / ct : 0;
      const stG    = statusOf(medG);
      gelEl.textContent = fmtHMS(medG);
      gelEl.className   = 'tme-mono ' + stG + '-val';
    }
  });

  // --- Times: TME ao vivo ---
  (appData.times || []).forEach(t => {
    const id  = 'time-ab-' + encodeId(t.nome);
    const el  = document.getElementById(id);
    if (!el) return;
    if (!t.leads_ab_isos || t.leads_ab_isos.length === 0) {
      el.textContent = '—';
      el.className   = 'time-metric-val ok-val';
      return;
    }
    const segs = t.leads_ab_isos.map(calcBizSeconds);
    const med  = segs.reduce((a,b)=>a+b,0) / segs.length;
    const st   = statusOf(med);
    el.textContent = fmtHMS(med);
    el.className   = 'time-metric-val ' + st + '-val';

    const gelEl = document.getElementById('time-ger-' + encodeId(t.nome));
    if (gelEl) {
      const abSum  = segs.reduce((a,b)=>a+b,0);
      const total  = (t.leads_fechados_tme_sum || 0) + abSum;
      const ct     = (t.leads_fechados_ct || 0) + segs.length;
      const medG   = ct > 0 ? total / ct : 0;
      const stG    = statusOf(medG);
      gelEl.textContent = fmtHMS(medG);
      gelEl.className   = 'time-metric-val ' + stG + '-val';
    }
  });

  // --- KPIs ao vivo ---
  const allAb = (appData.leads_aguardando || []).map(l => calcBizSeconds(l.effective_start));
  if (allAb.length > 0) {
    const medAb = allAb.reduce((a,b)=>a+b,0) / allAb.length;
    document.getElementById('k-ta').textContent = fmtHMS(medAb);
  }
}
setInterval(tick, 1000);

// ── ID SEGURO ──────────────────────────────────────────────────────
function encodeId(str) {
  return str.replace(/[^a-zA-Z0-9]/g, '_');
}

// ── RENDER FILA ───────────────────────────────────────────────────
function renderFila(leads) {
  const wrap = document.getElementById('fila-wrap');
  const ct   = document.getElementById('fila-ct');
  ct.textContent = leads.length + ' lead' + (leads.length !== 1 ? 's' : '');

  if (leads.length === 0) {
    wrap.innerHTML = `<div class="empty"><span class="empty-icon">✅</span><span class="empty-txt">Todos os leads foram atendidos hoje</span></div>`;
    return;
  }

  const tempMap = { quente:'temp-quente', morno:'temp-morno', frio:'temp-frio' };

  const rows = leads.map((l, i) => {
    const seg  = calcBizSeconds(l.effective_start);
    const st   = statusOf(seg);
    return `
      <tr class="r-${st}" data-eff="${l.effective_start}" style="animation-delay:${i*20}ms">
        <td class="td-sdr">${l.sdr_nome}</td>
        <td class="td-titulo" title="${l.titulo}">${l.titulo}</td>
        <td class="td-funil" title="${l.funil}">${l.funil}</td>
        <td>
          <span class="rede-lbl">${l.rede_label}</span>
          &nbsp;
          <span class="temp-badge ${tempMap[l.rede_temp] || 'temp-frio'}">${l.rede_temp_label}</span>
        </td>
        <td class="td-hora">${l.hora_criacao}</td>
        <td><span class="td-tme ${st}-val">${fmtHMS(seg)}</span></td>
      </tr>`;
  }).join('');

  wrap.innerHTML = `
    <table class="tbl">
      <thead><tr>
        <th>SDR</th><th>Lead</th><th>Funil</th>
        <th>Origem / Temperatura</th><th>Chegada</th><th>TME ⏱</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── RENDER TIMES ──────────────────────────────────────────────────
function renderTimes(times) {
  const wrap = document.getElementById('times-wrap');
  if (!times || times.length === 0) {
    wrap.innerHTML = `<div class="empty"><span class="empty-txt">Sem dados</span></div>`;
    return;
  }

  wrap.innerHTML = times.map((t, i) => {
    const abIsos = t.leads_ab_isos || [];
    const abSegs = abIsos.map(calcBizSeconds);
    const medAb  = abSegs.length ? abSegs.reduce((a,b)=>a+b,0)/abSegs.length : null;
    const abSum  = abSegs.reduce((a,b)=>a+b,0);
    const total  = (t.leads_fechados_tme_sum||0) + abSum;
    const ct     = (t.leads_fechados_ct||0) + abSegs.length;
    const medG   = ct > 0 ? total/ct : null;

    const stG  = medG  != null ? statusOf(medG)  : 'ok';
    const stAb = medAb != null ? statusOf(medAb) : 'ok';

    return `
      <div class="time-card" style="animation-delay:${i*40}ms">
        <div class="time-card-hdr">
          <span class="time-card-nome">${t.nome}</span>
          <span class="time-card-sdrs">${t.sdrs_count} SDR${t.sdrs_count !== 1 ? 's' : ''}</span>
        </div>
        <div class="time-metrics">
          <div class="time-metric">
            <span class="time-metric-lbl">TME Geral</span>
            <span class="time-metric-val ${stG}-val" id="time-ger-${encodeId(t.nome)}">${medG != null ? fmtHMS(medG) : '—'}</span>
          </div>
          <div class="time-metric">
            <span class="time-metric-lbl">TME Abertos</span>
            <span class="time-metric-val ${stAb}-val" id="time-ab-${encodeId(t.nome)}">${medAb != null ? fmtHMS(medAb) : '—'}</span>
          </div>
          <div class="time-metric">
            <span class="time-metric-lbl">Leads Hoje</span>
            <span class="time-metric-val">${t.leads_hoje}</span>
          </div>
          <div class="time-metric">
            <span class="time-metric-lbl">Em Aberto</span>
            <span class="time-metric-val ${t.leads_abertos > 0 ? 'warn-val' : 'ok-val'}">${t.leads_abertos}</span>
          </div>
        </div>
      </div>`;
  }).join('');
}

// ── RENDER SDRs ───────────────────────────────────────────────────
function renderSDRs(sdrs) {
  const wrap = document.getElementById('sdrs-wrap');
  if (!sdrs || sdrs.length === 0) {
    wrap.innerHTML = `<div class="empty"><span class="empty-txt">Sem dados</span></div>`;
    return;
  }

  const rows = sdrs.map((s, i) => {
    const abIsos = s.leads_abertos_iso || [];
    const abSegs = abIsos.map(calcBizSeconds);
    const medAb  = abSegs.length ? abSegs.reduce((a,b)=>a+b,0)/abSegs.length : null;

    const fTotal = (s.leads_fechados_tme_sum||0) + abSegs.reduce((a,b)=>a+b,0);
    const fCt    = (s.leads_fechados_ct||0) + abSegs.length;
    const medG   = fCt > 0 ? fTotal/fCt : null;

    const stG  = medG  != null ? statusOf(medG)  : 'ok';
    const stAb = medAb != null ? statusOf(medAb) : 'ok';
    const pct  = medAb != null ? barPct(medAb) : 0;
    const eid  = encodeId(s.nome);

    return `
      <tr style="animation-delay:${i*25}ms">
        <td><span class="sdr-nome">${s.nome}</span></td>
        <td><span class="sdr-time">${s.time || '—'}</span></td>
        <td class="td-r"><span class="sdr-ct ${s.leads_hoje === 0 ? 'zero' : ''}">${s.leads_hoje}</span></td>
        <td class="td-r"><span class="sdr-ct ${s.leads_abertos_ct === 0 ? 'zero' : ''}">${s.leads_abertos_ct}</span></td>
        <td class="td-r">
          <span class="tme-mono ${stG}-val" id="sdr-ger-${eid}">${medG != null ? fmtHMS(medG) : '—'}</span>
        </td>
        <td class="td-r">
          <span class="tme-mono ${stAb}-val" id="sdr-ab-${eid}">${medAb != null ? fmtHMS(medAb) : '—'}</span>
          <span class="mini-bar"><span class="mini-fill" id="bar-ab-${eid}" style="width:${pct}%;background:${barColor(stAb)}"></span></span>
        </td>
      </tr>`;
  }).join('');

  wrap.innerHTML = `
    <table class="tbl-sdrs">
      <thead><tr>
        <th>SDR</th>
        <th>Time</th>
        <th class="th-r">Leads Hoje</th>
        <th class="th-r">Em Aberto</th>
        <th class="th-r">TME Médio Geral</th>
        <th class="th-r">TME Médio Abertos ⏱</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── FETCH ──────────────────────────────────────────────────────────
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

    renderFila(data.leads_aguardando || []);
    renderTimes(data.times || []);
    renderSDRs(data.sdrs || []);
    startProgress();

  } catch(err) {
    console.error('Erro fetch:', err);
    document.getElementById('fila-wrap').innerHTML =
      `<div class="empty"><span class="empty-icon">⚠️</span><span class="empty-txt">Erro de conexão — tentando novamente</span></div>`;
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

# ── MAIN ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8052))
    print(f"\n📊  Board Academy — Monitor de TME")
    print(f"─" * 40)
    print(f"🌐  http://localhost:{port}")
    print(f"🔄  Refresh: 60s")
    print(f"─" * 40 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)
