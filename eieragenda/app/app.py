from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
import os
import json
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

APP_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("EIERAGENDA_DB_PATH", APP_DIR / "eieragenda.db"))
APP_VERSION = "v35-gunicorn-sqlite-wal"
HA_NOTIFY_ENTITY = os.environ.get("EIERAGENDA_HA_NOTIFY_ENTITY", "input_text.eieragenda_notificatie")
PRICE_FILE = Path(os.environ.get("EIERAGENDA_PRICE_FILE", "/share/eieragenda/eierprijzen.xlsx"))
_PRICE_CACHE = {"mtime": None, "tables": None, "error": None}

app = Flask(__name__)


def _last_sunday(year, month):
    d = date(year, month, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _amsterdam_offset_for_utc(dt_utc):
    # Europese zomertijd: van laatste zondag maart 01:00 UTC
    # t/m laatste zondag oktober 01:00 UTC. Zo wisselt de Eieragenda
    # om 00:00 Nederlandse tijd, niet om 00:00 UTC.
    start_day = _last_sunday(dt_utc.year, 3)
    end_day = _last_sunday(dt_utc.year, 10)
    start = datetime(dt_utc.year, 3, start_day.day, 1, 0, tzinfo=timezone.utc)
    end = datetime(dt_utc.year, 10, end_day.day, 1, 0, tzinfo=timezone.utc)
    return timedelta(hours=2) if start <= dt_utc < end else timedelta(hours=1)


def local_now():
    dt_utc = datetime.now(timezone.utc)
    return (dt_utc + _amsterdam_offset_for_utc(dt_utc)).replace(tzinfo=None)


def today_date():
    return local_now().date()



def ingress_base_path():
    """Basispad wanneer de app via Home Assistant Ingress draait.

    Normaal is dit leeg. Via hass_ingress is dit bijvoorbeeld
    /api/ingress/eieragenda. Daardoor blijven links, formulieren,
    redirects en statische bestanden werken binnen Home Assistant.
    """
    base = (
        request.headers.get("X-Ingress-Path")
        or request.headers.get("X-External-Path")
        or request.headers.get("X-Forwarded-Prefix")
        or ""
    ).strip()
    if base.startswith(("http://", "https://")):
        from urllib.parse import urlsplit
        base = urlsplit(base).path
    if base and not base.startswith("/"):
        base = "/" + base
    return base.rstrip("/")


def app_url(endpoint, **values):
    return ingress_base_path() + url_for(endpoint, **values)


@app.context_processor
def inject_ingress_helpers():
    return {
        "app_base": ingress_base_path(),
        "app_url": app_url,
    }

def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Timeout + busy_timeout zorgen dat korte gelijktijdige schrijfacties
    # niet direct een "database is locked" fout geven.
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def today_iso():
    return today_date().isoformat()

def now_iso():
    return local_now().isoformat(timespec="seconds")

def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return today_date()

def pretty_date(value):
    d = parse_date(value)
    dagen = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
    maanden = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
    if d == today_date():
        prefix = "Vandaag"
    elif d == today_date() + timedelta(days=1):
        prefix = "Morgen"
    else:
        prefix = dagen[d.weekday()].capitalize()
    return f"{prefix} · {d.day} {maanden[d.month-1]}"


def pretty_datetime(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        if dt.date() == today_date():
            return "vandaag " + dt.strftime("%H:%M")
        if dt.date() == today_date() - timedelta(days=1):
            return "gisteren " + dt.strftime("%H:%M")
        return dt.strftime("%d-%m-%Y %H:%M")
    except Exception:
        return value



def parse_money(value, default=0.0):
    if value is None:
        return default
    try:
        text = str(value).strip().replace("€", "").replace(" ", "").replace(",", ".")
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def money_text(value):
    try:
        return "€ " + f"{float(value):.2f}".replace(".", ",")
    except Exception:
        return "€ 0,00"


def price_per_piece_text(value):
    try:
        return "€ " + f"{float(value):.3f}".replace(".", ",")
    except Exception:
        return "€ 0,000"


def _xlsx_col_index(cell_ref):
    letters = ""
    for ch in cell_ref:
        if ch.isalpha():
            letters += ch.upper()
        else:
            break
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return max(n - 1, 0)


def _xlsx_cell_value(cell, shared_strings):
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        texts = [t.text or "" for t in cell.findall(".//a:t", ns)]
        return "".join(texts)
    v = cell.find("a:v", ns)
    if v is None or v.text is None:
        return ""
    raw = v.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except Exception:
            return raw
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except Exception:
        return raw


def _read_xlsx_sheet_rows(path):
    ns = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    result = {}
    with zipfile.ZipFile(path) as z:
        shared_strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                texts = [t.text or "" for t in si.findall(".//a:t", ns)]
                shared_strings.append("".join(texts))

        wb_root = ET.fromstring(z.read("xl/workbook.xml"))
        rel_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rels = {rel.attrib.get("Id"): rel.attrib.get("Target") for rel in rel_root.findall("rel:Relationship", ns)}

        for sheet in wb_root.findall("a:sheets/a:sheet", ns):
            name = sheet.attrib.get("name", "")
            rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rels.get(rid)
            if not target:
                continue
            sheet_path = "xl/" + target.lstrip("/")
            sheet_path = sheet_path.replace("xl/xl/", "xl/")
            if sheet_path not in z.namelist():
                continue
            sh_root = ET.fromstring(z.read(sheet_path))
            rows = []
            for row in sh_root.findall("a:sheetData/a:row", ns):
                values = {}
                max_col = -1
                for cell in row.findall("a:c", ns):
                    ref = cell.attrib.get("r", "A1")
                    col = _xlsx_col_index(ref)
                    values[col] = _xlsx_cell_value(cell, shared_strings)
                    max_col = max(max_col, col)
                if max_col >= 0:
                    rows.append([values.get(i, "") for i in range(max_col + 1)])
                else:
                    rows.append([])
            result[name] = rows
    return result


def _norm_header(value):
    return str(value or "").strip().lower().replace("é", "e")


def _num(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip().replace("€", "").replace(" ", "").replace(",", "."))
    except Exception:
        return None


def _extract_price_table(rows, sheet_name):
    header_row = None
    aantal_col = prijs_col = pps_col = None

    for ri, row in enumerate(rows[:12]):
        headers = [_norm_header(v) for v in row]
        for ci, h in enumerate(headers):
            if h == "prijs per stuk" or ("prijs" in h and "stuk" in h):
                pps_col = ci
        for ci, h in enumerate(headers):
            if h == "prijs":
                prijs_col = ci
        for ci, h in enumerate(headers):
            if h == "aantal" or h in ["1e soort", "2e soort", "dubbeldooiers", "dubbel"]:
                aantal_col = ci
        if pps_col is not None and prijs_col is not None:
            if aantal_col is None:
                aantal_col = max(prijs_col - 1, 0)
            header_row = ri
            break
        aantal_col = prijs_col = pps_col = None

    if header_row is None:
        return []

    table = []
    for row in rows[header_row + 1:]:
        def get(col):
            return row[col] if col is not None and col < len(row) else ""
        qty = _num(get(aantal_col))
        price = _num(get(prijs_col))
        pps = _num(get(pps_col))
        if qty is None or price is None:
            continue
        if qty <= 0:
            continue
        if pps is None or pps <= 0:
            pps = price / qty
        table.append({"aantal": int(round(qty)), "prijs": round(price, 2), "prijs_per_stuk": float(pps)})
    table.sort(key=lambda r: r["aantal"])
    return table


def load_price_tables(force=False):
    path = PRICE_FILE
    if not path.exists():
        return {}, f"Prijsbestand niet gevonden: {path}"
    try:
        mtime = path.stat().st_mtime
        if not force and _PRICE_CACHE.get("mtime") == mtime and _PRICE_CACHE.get("tables") is not None:
            return _PRICE_CACHE["tables"], _PRICE_CACHE.get("error")
        rows_by_sheet = _read_xlsx_sheet_rows(path)
        wanted = {
            "soort1": ["1e soort", "eerste soort", "1e"],
            "soort2": ["2e soort", "tweede soort", "2e"],
            "dubbeldooiers": ["dubbeldooiers", "dubbeldooier", "dubbel"],
        }
        tables = {}
        for key, names in wanted.items():
            found_name = None
            for sheet_name in rows_by_sheet:
                if _norm_header(sheet_name) in names:
                    found_name = sheet_name
                    break
            if found_name is None:
                # fallback: zoek of de naam erin voorkomt
                for sheet_name in rows_by_sheet:
                    sn = _norm_header(sheet_name)
                    if any(n in sn for n in names):
                        found_name = sheet_name
                        break
            tables[key] = _extract_price_table(rows_by_sheet.get(found_name, []), found_name or key)
        missing = [k for k, v in tables.items() if not v]
        error = "" if not missing else "Geen prijstabel gevonden voor: " + ", ".join(missing)
        _PRICE_CACHE.update({"mtime": mtime, "tables": tables, "error": error})
        return tables, error
    except Exception as e:
        _PRICE_CACHE.update({"mtime": None, "tables": {}, "error": str(e)})
        return {}, f"Prijsbestand kon niet gelezen worden: {e}"


def price_for_amount(kind, amount):
    try:
        amount = int(amount or 0)
    except Exception:
        amount = 0
    if amount <= 0:
        return 0.0, 0.0
    tables, _err = load_price_tables()
    table = tables.get(kind) or []
    if not table:
        return 0.0, 0.0

    chosen = None
    for row in table:
        if amount == row["aantal"]:
            return round(row["prijs"], 2), float(row["prijs_per_stuk"])
        if row["aantal"] <= amount:
            chosen = row
        elif row["aantal"] > amount:
            break
    if chosen is None:
        chosen = table[0]
    pps = float(chosen["prijs_per_stuk"] or (chosen["prijs"] / chosen["aantal"]))
    return round(amount * pps + 1e-9, 2), pps


def row_get(row, key, default=None):
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
        if isinstance(row, dict):
            return row.get(key, default)
    except Exception:
        pass
    return default


def calculate_order_price_live(row):
    s1 = int(row_get(row, "soort1", 0) or 0)
    s2 = int(row_get(row, "soort2", 0) or 0)
    dd = int(row_get(row, "dubbeldooiers", 0) or 0)
    fixed = int(row_get(row, "vaste_prijs_actief", 0) or 0) == 1
    fixed_pps = parse_money(row_get(row, "vaste_prijs_per_ei", 0), 0)
    details = []
    if fixed:
        total_eggs = s1 + s2 + dd
        total = round(total_eggs * fixed_pps + 1e-9, 2)
        if total_eggs:
            details.append({"label": "Vaste prijs", "aantal": total_eggs, "prijs": total, "prijs_per_stuk": fixed_pps})
        return {"total": total, "details": details, "mode": "vast", "mode_label": "Vaste prijs"}

    total = 0.0
    for kind, label, amount in [
        ("soort1", "1e soort", s1),
        ("soort2", "2e soort", s2),
        ("dubbeldooiers", "Dubbel", dd),
    ]:
        price, pps = price_for_amount(kind, amount)
        total += price
        if amount:
            details.append({"label": label, "aantal": amount, "prijs": price, "prijs_per_stuk": pps})
    return {"total": round(total, 2), "details": details, "mode": "lijst", "mode_label": "Volgens prijslijst"}


def calculate_order_price(row):
    """Geef de prijs van een bestelling.

    Vanaf v14 wordt de prijs bij aanmaken/wijzigen opgeslagen in de database.
    Daardoor blijven oude bestellingen hetzelfde bedrag houden, ook als later
    eierprijzen.xlsx wordt aangepast. Oude regels zonder opgeslagen prijs vallen
    terug op live berekenen uit de huidige Excel.
    """
    stored = row_get(row, "prijs_totaal", None)
    if stored not in (None, ""):
        try:
            total = round(float(stored), 2)
            mode = (row_get(row, "prijs_bron", "") or "").strip()
            label = (row_get(row, "prijs_label", "") or "").strip()
            if not label:
                if mode == "vast":
                    fixed_pps = parse_money(row_get(row, "vaste_prijs_per_ei", 0), 0)
                    label = "Vaste prijs"
                elif mode == "lijst":
                    label = "Volgens prijslijst"
                else:
                    label = "Opgeslagen prijs"
            return {"total": total, "details": [], "mode": mode or "opgeslagen", "mode_label": label}
        except Exception:
            pass
    return calculate_order_price_live(row)


def persist_order_price(conn, order_id):
    """Bereken prijs opnieuw en sla vast op bij de bestelling."""
    row = conn.execute("SELECT * FROM bestellingen WHERE id=?", (order_id,)).fetchone()
    if not row:
        return
    pricing = calculate_order_price_live(row)
    conn.execute(
        "UPDATE bestellingen SET prijs_totaal=?, prijs_bron=?, prijs_label=? WHERE id=?",
        (round(pricing["total"], 2), pricing["mode"], pricing["mode_label"], order_id),
    )


def price_tables_for_view():
    tables, error = load_price_tables(force=True)
    view = {}
    labels = {"soort1": "1e soort", "soort2": "2e soort", "dubbeldooiers": "Dubbeldooiers"}
    for key in ["soort1", "soort2", "dubbeldooiers"]:
        view[key] = {
            "label": labels[key],
            "rows": [dict(r, prijs_mooi=money_text(r["prijs"]), prijs_per_stuk_mooi=price_per_piece_text(r["prijs_per_stuk"])) for r in tables.get(key, [])]
        }
    return view, error



def month_name(n):
    maanden = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"]
    try:
        return maanden[int(n)-1]
    except Exception:
        return str(n)


def period_key_label(d, periode):
    """Maak sleutel/label voor Totalen pagina."""
    if periode == "week":
        iso = d.isocalendar()
        monday = d - timedelta(days=d.weekday())
        sunday = monday + timedelta(days=6)
        key = f"{iso.year}-W{iso.week:02d}"
        label = f"Week {iso.week} · {iso.year}"
        sub = f"{monday.strftime('%d-%m-%Y')} t/m {sunday.strftime('%d-%m-%Y')}"
        return key, label, sub
    if periode == "maand":
        key = f"{d.year}-{d.month:02d}"
        label = f"{month_name(d.month).capitalize()} {d.year}"
        return key, label, ""
    if periode == "jaar":
        key = f"{d.year}"
        return key, str(d.year), ""
    key = d.isoformat()
    return key, compact_date(d.isoformat()), pretty_date(d.isoformat())


def empty_total():
    return {
        "bestellingen": 0,
        "klanten": set(),
        "soort1": 0,
        "soort2": 0,
        "dubbeldooiers": 0,
        "eieren": 0,
        "bedrag": 0.0,
    }


def finalize_total(t):
    t = dict(t)
    klanten = t.get("klanten", set())
    t["klanten_aantal"] = len(klanten) if isinstance(klanten, set) else int(klanten or 0)
    t.pop("klanten", None)
    t["bedrag"] = round(float(t.get("bedrag", 0) or 0), 2)
    t["bedrag_mooi"] = money_text(t["bedrag"])
    t["soort1_stack"] = amount_text(t.get("soort1", 0))
    t["soort2_stack"] = amount_text(t.get("soort2", 0))
    t["dubbeldooiers_stack"] = amount_text(t.get("dubbeldooiers", 0))
    return t


def build_totalen(conn, periode="dag", status="voltooid", vanaf="", tot=""):
    periode = periode if periode in ["dag", "week", "maand", "jaar"] else "dag"
    status = status if status in ["voltooid", "open", "alles"] else "voltooid"
    vanaf_d = parse_date(vanaf) if vanaf else None
    tot_d = parse_date(tot) if tot else None

    rows = conn.execute("""
        SELECT b.*, k.naam AS klant_naam, k.telefoon, k.email, k.adres
        FROM bestellingen b
        JOIN klanten k ON k.id = b.klant_id
        ORDER BY b.ophaal_datum DESC, b.id DESC
    """).fetchall()

    groups = {}
    summary = empty_total()

    for r in rows:
        verwerkt = int(r["verwerkt"] or 0) == 1
        if status == "voltooid" and not verwerkt:
            continue
        if status == "open" and verwerkt:
            continue

        if status == "voltooid":
            datum_raw = (r["voltooid_op"] or "")[:10] or r["ophaal_datum"]
        elif status == "open":
            datum_raw = r["ophaal_datum"]
        else:
            datum_raw = ((r["voltooid_op"] or "")[:10] if verwerkt else "") or r["ophaal_datum"]

        d = parse_date(datum_raw)
        if vanaf_d and d < vanaf_d:
            continue
        if tot_d and d > tot_d:
            continue

        key, label, sub = period_key_label(d, periode)
        if key not in groups:
            groups[key] = {"key": key, "label": label, "sub": sub, **empty_total()}

        pricing = calculate_order_price(r)
        bedrag = float(pricing.get("total", 0) or 0)
        s1 = int(r["soort1"] or 0)
        s2 = int(r["soort2"] or 0)
        dd = int(r["dubbeldooiers"] or 0)

        for target in (groups[key], summary):
            target["bestellingen"] += 1
            target["klanten"].add(r["klant_id"])
            target["soort1"] += s1
            target["soort2"] += s2
            target["dubbeldooiers"] += dd
            target["eieren"] += s1 + s2 + dd
            target["bedrag"] += bedrag

    group_list = []
    for key, g in groups.items():
        gf = finalize_total(g)
        gf["key"] = key
        gf["label"] = g["label"]
        gf["sub"] = g.get("sub", "")
        group_list.append(gf)
    group_list.sort(key=lambda x: x["key"], reverse=True)

    return finalize_total(summary), group_list

def init_db():
    with db() as conn:
        # WAL maakt SQLite geschikter voor meerdere gebruikers tegelijk:
        # lezen kan doorgaan terwijl kort wordt geschreven.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS klanten (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL UNIQUE COLLATE NOCASE,
            telefoon TEXT DEFAULT '',
            email TEXT DEFAULT '',
            adres TEXT DEFAULT '',
            actief INTEGER DEFAULT 1,
            bijgewerkt_op TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS bestellingen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klant_id INTEGER NOT NULL,
            ophaal_datum TEXT NOT NULL,
            tijd_type TEXT DEFAULT 'none',
            tijd_van TEXT DEFAULT '',
            tijd_tot TEXT DEFAULT '',
            tijd_extra TEXT DEFAULT '',
            soort1 INTEGER DEFAULT 0,
            soort2 INTEGER DEFAULT 0,
            dubbeldooiers INTEGER DEFAULT 0,
            factuur INTEGER DEFAULT 0,
            factuur_meegeven INTEGER DEFAULT 0,
            pinnen INTEGER DEFAULT 0,
            contant INTEGER DEFAULT 0,
            vaste_prijs_actief INTEGER DEFAULT 0,
            vaste_prijs_per_ei REAL DEFAULT 0,
            prijs_totaal REAL DEFAULT NULL,
            prijs_bron TEXT DEFAULT '',
            prijs_label TEXT DEFAULT '',
            opmerking TEXT DEFAULT '',
            verwerkt INTEGER DEFAULT 0,
            voltooid_op TEXT DEFAULT '',
            aangemaakt_op TEXT NOT NULL,
            bijgewerkt_op TEXT NOT NULL,
            FOREIGN KEY (klant_id) REFERENCES klanten(id)
        );
        """)
        b_cols = [r["name"] for r in conn.execute("PRAGMA table_info(bestellingen)").fetchall()]
        for col, definition in {
            "tijd_type": "TEXT DEFAULT 'none'",
            "tijd_van": "TEXT DEFAULT ''",
            "tijd_tot": "TEXT DEFAULT ''",
            "tijd_extra": "TEXT DEFAULT ''",
            "factuur": "INTEGER DEFAULT 0",
            "factuur_meegeven": "INTEGER DEFAULT 0",
            "pinnen": "INTEGER DEFAULT 0",
            "contant": "INTEGER DEFAULT 0",
            "vaste_prijs_actief": "INTEGER DEFAULT 0",
            "vaste_prijs_per_ei": "REAL DEFAULT 0",
            "prijs_totaal": "REAL DEFAULT NULL",
            "prijs_bron": "TEXT DEFAULT ''",
            "prijs_label": "TEXT DEFAULT ''",
            "verwerkt": "INTEGER DEFAULT 0",
            "voltooid_op": "TEXT DEFAULT ''",
        }.items():
            if col not in b_cols:
                conn.execute(f"ALTER TABLE bestellingen ADD COLUMN {col} {definition}")
        k_cols = [r["name"] for r in conn.execute("PRAGMA table_info(klanten)").fetchall()]
        if "adres" not in k_cols:
            conn.execute("ALTER TABLE klanten ADD COLUMN adres TEXT DEFAULT ''")
        if "email" not in k_cols:
            conn.execute("ALTER TABLE klanten ADD COLUMN email TEXT DEFAULT ''")
        if "actief" not in k_cols:
            conn.execute("ALTER TABLE klanten ADD COLUMN actief INTEGER DEFAULT 1")
        if "bijgewerkt_op" not in k_cols:
            conn.execute("ALTER TABLE klanten ADD COLUMN bijgewerkt_op TEXT DEFAULT ''")

# Bij Gunicorn wordt __main__ niet uitgevoerd. Initialiseer de database daarom
# al bij import/start van de app. Niet meer op elk verzoek; dat scheelt locks.
init_db()

def amount_text(amount):
    amount = int(amount or 0)
    if amount < 30:
        return "" if amount == 0 else f"{amount} eieren"

    stapels = amount // 180
    rest = amount % 180
    bladen = rest // 30
    eieren = rest % 30

    parts = []
    if stapels:
        parts.append(f"{stapels} stapel" if stapels == 1 else f"{stapels} stapels")
    if bladen:
        parts.append(f"{bladen} blad" if bladen == 1 else f"{bladen} bladen")
    if eieren:
        parts.append(f"{eieren} eieren")

    return " en ".join(parts)


def time_label(row):
    t = row["tijd_type"] or "none"
    van = row["tijd_van"] or ""
    tot = row["tijd_tot"] or ""
    extra = row["tijd_extra"] or ""
    if t == "exact" and van:
        return van
    if t == "range" and van and tot:
        return f"{van} - {tot}"
    if t == "manual" and extra:
        return extra
    return "Tijd niet bekend"


def compact_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d-%m-%Y")
    except Exception:
        return value or ""


def notification_date_text(value):
    """Datum voor WhatsApp/HA notificaties.

    Niet als 04-06-2026, want WhatsApp herkent dat soms als telefoonnummer/link.
    Daarom tekstvorm: 4 juni 2026.
    """
    maanden = {
        1: "januari",
        2: "februari",
        3: "maart",
        4: "april",
        5: "mei",
        6: "juni",
        7: "juli",
        8: "augustus",
        9: "september",
        10: "oktober",
        11: "november",
        12: "december",
    }
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
        return f"{d.day} {maanden.get(d.month, d.month)} {d.year}"
    except Exception:
        return value or ""


def notification_amounts(row):
    parts = []
    try:
        s1 = int(row["soort1"] or 0)
        s2 = int(row["soort2"] or 0)
        dd = int(row["dubbeldooiers"] or 0)
    except Exception:
        s1 = s2 = dd = 0

    def one(amount, label):
        if not amount:
            return None
        extra = amount_text(amount).replace(" en ", " + ").replace(" eieren", " los")
        if amount >= 30 and extra:
            return f"{amount} {label} ({extra})"
        return f"{amount} {label}"

    for item in [one(s1, "1e soort"), one(s2, "2e soort"), one(dd, "dubbeldooiers")]:
        if item:
            parts.append(item)
    return " + ".join(parts) if parts else "geen eieren"


def notification_payment_text(row):
    """Korte betaaltekst voor HA/WhatsApp meldingen."""
    labels = []
    try:
        pinnen = int(row["pinnen"] or 0) == 1
        contant = int(row["contant"] or 0) == 1
        factuur = int(row["factuur"] or 0) == 1
        factuur_meegeven = int(row["factuur_meegeven"] or 0) == 1
    except Exception:
        pinnen = contant = factuur = factuur_meegeven = False

    if pinnen and contant:
        labels.append("Pinnen of Contant")
    else:
        if pinnen:
            labels.append("Pinnen")
        if contant:
            labels.append("Contant")

    if factuur:
        labels.append("Factuur mailen")
    if factuur_meegeven:
        labels.append("Factuur meegeven")

    return " · ".join(labels) if labels else "Betaling onbekend"


def notification_price_text(row):
    try:
        pricing = calculate_order_price(row)
        bedrag = money_text(pricing.get("total", 0)).replace("€ ", "€")
        mode = (pricing.get("mode") or "").strip()
        label = (pricing.get("mode_label") or "").strip().lower()
        if mode == "vast" or "vast" in label:
            bron = "Vaste prijs"
        elif mode == "lijst" or "lijst" in label:
            bron = "Volgens prijslijst"
        else:
            bron = "Prijs"
        return f"{bedrag} {bron}"
    except Exception:
        return f"{money_text(0).replace('€ ', '€')} Prijs"


def notification_amount_lines(row):
    """Aantallen per soort als losse regels voor Home Assistant webhook.

    Hiermee hoeft de HA automation niet meer te splitsen op plus-tekens.
    """
    lines = []
    try:
        s1 = int(row["soort1"] or 0)
        s2 = int(row["soort2"] or 0)
        dd = int(row["dubbeldooiers"] or 0)
    except Exception:
        s1 = s2 = dd = 0

    def one(amount, label):
        if not amount:
            return None
        extra = amount_text(amount).replace(" en ", " + ").replace(" eieren", " los")
        if amount >= 30 and extra:
            return f"{amount} {label} ({extra})"
        return f"{amount} {label}"

    for item in [one(s1, "1e soort"), one(s2, "2e soort"), one(dd, "dubbeldooiers")]:
        if item:
            lines.append(item)
    return lines or ["geen eieren"]


def build_notification_payload(kind, row):
    """Maak een gestructureerde webhook-payload.

    Geen emoji's hier; die komen in de Home Assistant automation.
    De oude 'message' blijft als fallback aanwezig, maar wordt niet meer op 255 tekens afgekapt.
    """
    naam = (row["klant_naam"] or "Onbekende klant").strip()
    adres = (row["adres"] or "").strip()
    klant = f"{naam} - {adres}" if adres else naam
    eieren_lines = notification_amount_lines(row)
    eieren = " + ".join(eieren_lines)
    betaling = notification_payment_text(row)
    prijs = notification_price_text(row)
    datum = notification_date_text(row["ophaal_datum"])
    tijd = time_label(row)
    aangemaakt = local_now().strftime("%H:%M:%S")
    nummer = f"#{row['id']}"

    # Pipe-message blijft beschikbaar voor bestaande automations, maar de voorkeur is trigger.json velden gebruiken.
    message = f"{kind} | {klant} | {eieren} | {betaling} | {prijs} | {datum} | {tijd} | {nummer} | {aangemaakt}"

    return {
        "message": message,
        "status": kind,
        "naam": naam,
        "adres": adres,
        "klant": klant,
        "eieren": eieren,
        "eieren_regels": eieren_lines,
        "betaling": betaling,
        "prijs": prijs,
        "datum": datum,
        "tijd": tijd,
        "nummer": nummer,
        "aangemaakt": aangemaakt,
        "entity_id": HA_NOTIFY_ENTITY,
    }

def fetch_order_for_notification(conn, order_id):
    return conn.execute("""
        SELECT b.*, k.naam AS klant_naam, k.telefoon, k.email, k.adres
        FROM bestellingen b
        JOIN klanten k ON k.id = b.klant_id
        WHERE b.id=?
    """, (order_id,)).fetchone()


def post_ha_webhook(payload_data):
    """Stuur notificatie naar Home Assistant via webhook.

    De payload is gestructureerd JSON, zodat Home Assistant niet meer hoeft te splitsen op tekst.
    """
    webhook_id = os.environ.get("EIERAGENDA_HA_WEBHOOK_ID", "eieragenda_notificatie")
    if isinstance(payload_data, str):
        payload_data = {"message": payload_data, "entity_id": HA_NOTIFY_ENTITY}
    else:
        payload_data.setdefault("entity_id", HA_NOTIFY_ENTITY)
    payload = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")

    # Probeer meerdere interne HA adressen. De eerste die werkt is genoeg.
    urls = [
        f"http://supervisor/core/api/webhook/{webhook_id}",
        f"http://homeassistant.local:8123/api/webhook/{webhook_id}",
        f"http://homeassistant:8123/api/webhook/{webhook_id}",
        f"http://172.30.32.1:8123/api/webhook/{webhook_id}",
    ]

    last_error = None
    for url in urls:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
            print(f"[Eieragenda] HA webhook notificatie verzonden via {url}: {payload_data.get('message', payload_data)}", flush=True)
            return True
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            last_error = f"HTTP {e.code} bij {url} {body}"
        except Exception as e:
            last_error = f"{url}: {e}"

    print(f"[Eieragenda] HA webhook notificatie mislukt: {last_error}", flush=True)
    return False


def send_ha_notification(payload_data):
    # Alleen webhook gebruiken; geen directe Home Assistant API-token nodig.
    post_ha_webhook(payload_data)

def notify_order_event(conn, kind, order_id):
    row = fetch_order_for_notification(conn, order_id)
    if not row:
        print(f"[Eieragenda] Geen bestelling gevonden voor notificatie: {order_id}", flush=True)
        return
    send_ha_notification(build_notification_payload(kind, row))

def time_sort_sql():
    return """
    CASE
      WHEN b.tijd_type='none' THEN '00:00'
      WHEN b.tijd_type='exact' AND b.tijd_van != '' THEN b.tijd_van
      WHEN b.tijd_type='range' AND b.tijd_van != '' THEN b.tijd_van
      ELSE '00:01'
    END
    """

def form_has_fixed_price(form):
    return (form.get("prijs_type") == "vast") or (form.get("vaste_prijs_actief") in ["on", "1", "true", "True"])

def validate_order_form(form):
    klant_id = form.get("klant_id", "")
    nieuwe_klant = form.get("nieuwe_klant", "").strip()
    s1 = int(form.get("soort1") or 0)
    s2 = int(form.get("soort2") or 0)
    dd = int(form.get("dubbeldooiers") or 0)
    if not klant_id and not nieuwe_klant:
        return False, "Niet alle velden ingevuld: kies een klant of maak een nieuwe klant aan."
    if klant_id == "__new__" and not nieuwe_klant:
        return False, "Niet alle velden ingevuld: vul de naam van de nieuwe klant in."
    if s1 + s2 + dd <= 0:
        return False, "Niet alle velden ingevuld: vul minimaal één aantal eieren in."
    if form_has_fixed_price(form) and parse_money(form.get("vaste_prijs_per_ei"), 0) <= 0:
        return False, "Vul een vaste prijs per ei in, of zet vaste prijs uit."
    return True, ""

def ensure_customer(conn, form):
    klant_id = form.get("klant_id", "")
    nieuwe_klant = form.get("nieuwe_klant", "").strip()
    telefoon = form.get("telefoon", "").strip()
    email = form.get("email", "").strip()
    adres = form.get("adres", "").strip()
    if klant_id == "__new__":
        existing = conn.execute("SELECT id FROM klanten WHERE lower(naam)=lower(?)", (nieuwe_klant,)).fetchone()
        if existing:
            conn.execute("UPDATE klanten SET actief=1, telefoon=COALESCE(NULLIF(?, ''), telefoon), email=COALESCE(NULLIF(?, ''), email), adres=COALESCE(NULLIF(?, ''), adres), bijgewerkt_op=? WHERE id=?", (telefoon, email, adres, now_iso(), existing["id"]))
            return existing["id"]
        conn.execute("INSERT INTO klanten (naam, telefoon, email, adres, actief, bijgewerkt_op) VALUES (?, ?, ?, ?, 1, ?)", (nieuwe_klant, telefoon, email, adres, now_iso()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return klant_id

def decorate_order(r):
    order = dict(r)
    order["time_label"] = time_label(r)
    order["soort1_stack"] = amount_text(r["soort1"])
    order["soort2_stack"] = amount_text(r["soort2"])
    order["dubbeldooiers_stack"] = amount_text(r["dubbeldooiers"])
    order["aangemaakt_mooi"] = pretty_datetime(r["aangemaakt_op"])
    order["voltooid_mooi"] = pretty_datetime(r["voltooid_op"]) if "voltooid_op" in r.keys() else ""
    pricing = calculate_order_price(order)
    order["prijs_totaal"] = pricing["total"]
    order["prijs_totaal_mooi"] = money_text(pricing["total"])
    order["prijs_label"] = pricing["mode_label"]
    order["prijs_mode"] = pricing["mode"]
    if pricing["mode"] == "lijst":
        order["prijs_label_kort"] = "Volgens prijslijst"
    elif pricing["mode"] == "vast":
        order["prijs_label_kort"] = "Vaste prijs"
    else:
        order["prijs_label_kort"] = pricing["mode_label"] or "Prijs"
    return order

def build_groups(conn, history=False, q="", datum=""):
    q = (q or "").strip()
    datum = (datum or "").strip()

    if history:
        where = "1=1"
        params = []
        order_sql = f"""
          b.verwerkt ASC,
          CASE WHEN b.verwerkt=0 THEN b.ophaal_datum END ASC,
          CASE WHEN b.verwerkt=0 THEN {time_sort_sql()} END ASC,
          CASE WHEN b.verwerkt=1 THEN COALESCE(NULLIF(b.voltooid_op, ''), b.bijgewerkt_op, b.aangemaakt_op) END DESC,
          b.ophaal_datum DESC,
          k.naam
        """
    else:
        # Toon vandaag/toekomst, maar óók oude bestellingen die nog niet voltooid zijn.
        where = "(b.ophaal_datum >= ? OR b.verwerkt = 0)"
        params = [today_iso()]
        order_sql = f"""
          b.ophaal_datum ASC,
          b.verwerkt ASC,
          CASE WHEN b.verwerkt=1 THEN COALESCE(NULLIF(b.voltooid_op, ''), b.bijgewerkt_op, b.aangemaakt_op) END DESC,
          CASE WHEN b.verwerkt=0 THEN {time_sort_sql()} END ASC,
          k.naam
        """

    if q:
        where += """
            AND (
                k.naam LIKE ?
                OR k.telefoon LIKE ?
                OR k.adres LIKE ?
                OR b.opmerking LIKE ?
            )
        """
        like = f"%{q}%"
        params.extend([like, like, like, like])

    if datum:
        where += " AND b.ophaal_datum = ?"
        params.append(datum)

    rows = conn.execute(f"""
        SELECT b.*, k.naam AS klant_naam, k.telefoon, k.email, k.adres
        FROM bestellingen b
        JOIN klanten k ON k.id = b.klant_id
        WHERE {where}
        ORDER BY
          {order_sql}
    """, tuple(params)).fetchall()

    grouped = []
    groups_by_date = {}
    for r in rows:
        current_date = r["ophaal_datum"]
        if history and r["verwerkt"]:
            # In geschiedenis groeperen we voltooide bestellingen op voltooid-datum.
            # Open bestellingen blijven op ophaaldatum bovenaan staan.
            done_dt = ""
            try:
                done_dt = (r["voltooid_op"] or "")[:10]
            except Exception:
                done_dt = ""
            if done_dt:
                current_date = done_dt
        if current_date not in groups_by_date:
            groups_by_date[current_date] = {
                "datum": current_date,
                "datum_mooi": pretty_date(current_date),
                "is_overdue": parse_date(current_date) < today_date(),
                "orders": [],
                "remaining": {"soort1": 0, "soort2": 0, "dubbeldooiers": 0, "aantal": 0, "prijs_totaal": 0.0},
            }
            grouped.append(groups_by_date[current_date])
        g = groups_by_date[current_date]
        decorated = decorate_order(r)
        g["orders"].append(decorated)
        if not r["verwerkt"]:
            for key in ["soort1", "soort2", "dubbeldooiers"]:
                g["remaining"][key] += r[key]
            g["remaining"]["aantal"] += 1
            g["remaining"]["prijs_totaal"] += decorated.get("prijs_totaal", 0) or 0

    for g in grouped:
        g["remaining"]["soort1_stack"] = amount_text(g["remaining"]["soort1"])
        g["remaining"]["soort2_stack"] = amount_text(g["remaining"]["soort2"])
        g["remaining"]["dubbeldooiers_stack"] = amount_text(g["remaining"]["dubbeldooiers"])
        g["remaining"]["prijs_mooi"] = money_text(g["remaining"].get("prijs_totaal", 0))
    return grouped


def state_token(conn):
    row = conn.execute("""
        SELECT
          COALESCE(MAX(bijgewerkt_op),'') AS bmax,
          (SELECT COALESCE(MAX(bijgewerkt_op),'') FROM klanten) AS kmax,
          COUNT(*) AS count
        FROM bestellingen
    """).fetchone()
    return f"{row['bmax']}-{row['kmax']}-{row['count']}"

@app.route("/")
def index():
    with db() as conn:
        klanten = conn.execute("SELECT * FROM klanten WHERE actief=1 ORDER BY naam").fetchall()
        grouped = build_groups(conn, history=False)
        token = state_token(conn)
    return render_template("index.html", klanten=klanten, grouped=grouped, today=today_iso(), state_token=token, history=False, body_class="page-agenda")

@app.route("/geschiedenis")
def geschiedenis():
    q = request.args.get("q", "").strip()
    datum = request.args.get("datum", "").strip()
    with db() as conn:
        klanten = conn.execute("SELECT * FROM klanten WHERE actief=1 ORDER BY naam").fetchall()
        grouped = build_groups(conn, history=True, q=q, datum=datum)
        token = state_token(conn)
    return render_template("geschiedenis.html", klanten=klanten, grouped=grouped, today=today_iso(), state_token=token, history=True, q=q, datum=datum, body_class="page-static page-history")



@app.route("/prijzen")
def prijzen():
    def count_arg(name):
        try:
            return max(int(request.args.get(name, "0") or 0), 0)
        except Exception:
            return 0

    amounts = {
        "soort1": count_arg("soort1"),
        "soort2": count_arg("soort2"),
        "dubbeldooiers": count_arg("dubbeldooiers"),
    }
    details = []
    total = 0.0
    for key, label in [("soort1", "1e soort"), ("soort2", "2e soort"), ("dubbeldooiers", "Dubbeldooiers")]:
        price, pps = price_for_amount(key, amounts[key])
        total += price
        details.append({
            "key": key,
            "label": label,
            "aantal": amounts[key],
            "prijs": price,
            "prijs_mooi": money_text(price),
            "prijs_per_stuk_mooi": price_per_piece_text(pps),
        })
    tables, price_error = price_tables_for_view()
    return render_template(
        "prijzen.html",
        amounts=amounts,
        details=details,
        total=round(total, 2),
        total_mooi=money_text(total),
        tables=tables,
        price_error=price_error,
        price_file=str(PRICE_FILE),
        state_token="",
        body_class="page-static",
    )


@app.route("/totalen")
def totalen():
    periode = request.args.get("periode", "dag")
    status = request.args.get("status", "voltooid")
    vanaf = request.args.get("vanaf", "")
    tot = request.args.get("tot", "")
    with db() as conn:
        summary, groups = build_totalen(conn, periode=periode, status=status, vanaf=vanaf, tot=tot)
    status_label = {
        "voltooid": "Alleen voltooide bestellingen",
        "open": "Alleen open bestellingen",
        "alles": "Open en voltooide bestellingen",
    }.get(status, "Alleen voltooide bestellingen")
    periode_label = {
        "dag": "per dag",
        "week": "per week",
        "maand": "per maand",
        "jaar": "per jaar",
    }.get(periode, "per dag")
    return render_template(
        "totalen.html",
        periode=periode,
        status=status,
        vanaf=vanaf,
        tot=tot,
        status_label=status_label,
        periode_label=periode_label,
        summary=summary,
        groups=groups,
        state_token="",
        body_class="page-static page-totalen",
    )

@app.route("/api/prijzen/bereken")
def api_prijzen_bereken():
    def safe_int_arg(name):
        try:
            return max(0, int(float(str(request.args.get(name) or 0).replace(",", "."))))
        except Exception:
            return 0
    row = {
        "soort1": safe_int_arg("soort1"),
        "soort2": safe_int_arg("soort2"),
        "dubbeldooiers": safe_int_arg("dubbeldooiers"),
        "vaste_prijs_actief": 0,
        "vaste_prijs_per_ei": 0,
    }
    pricing = calculate_order_price_live(row)
    return jsonify({
        "ok": True,
        "total": pricing["total"],
        "total_mooi": money_text(pricing["total"]),
        "details": [
            {
                "label": d["label"],
                "aantal": d["aantal"],
                "prijs": d["prijs"],
                "prijs_mooi": money_text(d["prijs"]),
                "prijs_per_stuk_mooi": price_per_piece_text(d["prijs_per_stuk"]),
            }
            for d in pricing.get("details", [])
        ],
    })


@app.route("/bestelling/nieuw", methods=["POST"])
def bestelling_nieuw():
    ok, msg = validate_order_form(request.form)
    if not ok:
        return redirect(app_url("index", error=msg))
    with db() as conn:
        klant_id = ensure_customer(conn, request.form)
        now = now_iso()
        tijd_type = request.form.get("tijd_type") or "none"
        tijd_van = request.form.get("tijd_van", "").strip()
        tijd_tot = request.form.get("tijd_tot", "").strip()
        if tijd_type not in ["exact", "range"]:
            tijd_van = ""
        if tijd_type != "range":
            tijd_tot = ""
        conn.execute("""
            INSERT INTO bestellingen
            (klant_id, ophaal_datum, tijd_type, tijd_van, tijd_tot, tijd_extra,
             soort1, soort2, dubbeldooiers, factuur, factuur_meegeven, pinnen, contant, vaste_prijs_actief, vaste_prijs_per_ei, opmerking, verwerkt, aangemaakt_op, bijgewerkt_op)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (
            klant_id, request.form.get("ophaal_datum") or today_iso(),
            tijd_type, tijd_van, tijd_tot, request.form.get("tijd_extra", "").strip(),
            int(request.form.get("soort1") or 0), int(request.form.get("soort2") or 0), int(request.form.get("dubbeldooiers") or 0),
            1 if request.form.get("factuur") == "on" else 0,
            1 if request.form.get("factuur_meegeven") == "on" else 0,
            1 if request.form.get("pinnen") == "on" else 0,
            1 if request.form.get("contant") == "on" else 0,
            1 if form_has_fixed_price(request.form) else 0,
            parse_money(request.form.get("vaste_prijs_per_ei"), 0),
            request.form.get("opmerking", "").strip(), now, now
        ))
        new_order_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        persist_order_price(conn, new_order_id)
        notify_order_event(conn, "Nieuwe bestelling", new_order_id)
    return redirect(app_url("index"))

@app.route("/bestelling/<int:id>/bewerken", methods=["GET", "POST"])
def bestelling_bewerken(id):
    with db() as conn:
        if request.method == "POST":
            ok, msg = validate_order_form(request.form)
            if not ok:
                return redirect(app_url("bestelling_bewerken", id=id, error=msg))

            klant_id = ensure_customer(conn, request.form)
            now = now_iso()
            tijd_type = request.form.get("tijd_type") or "none"
            tijd_van = request.form.get("tijd_van", "").strip()
            tijd_tot = request.form.get("tijd_tot", "").strip()

            if tijd_type not in ["exact", "range"]:
                tijd_van = ""
            if tijd_type != "range":
                tijd_tot = ""

            verwerkt = 1 if request.form.get("verwerkt") == "on" else 0
            current = conn.execute("SELECT verwerkt, voltooid_op FROM bestellingen WHERE id=?", (id,)).fetchone()
            current_verwerkt = int(current["verwerkt"] or 0) if current and "verwerkt" in current.keys() else 0
            current_voltooid = current["voltooid_op"] if current and "voltooid_op" in current.keys() else ""

            if verwerkt and not current_voltooid:
                voltooid_op = now
            elif not verwerkt:
                voltooid_op = ""
            else:
                voltooid_op = current_voltooid

            notification_kind = "Bestelling voltooid" if verwerkt and not current_verwerkt else "Bestelling gewijzigd"

            conn.execute("""
                UPDATE bestellingen
                SET klant_id=?, ophaal_datum=?, tijd_type=?, tijd_van=?, tijd_tot=?, tijd_extra=?,
                    soort1=?, soort2=?, dubbeldooiers=?, factuur=?, factuur_meegeven=?, pinnen=?, contant=?, vaste_prijs_actief=?, vaste_prijs_per_ei=?, opmerking=?,
                    verwerkt=?, voltooid_op=?, bijgewerkt_op=?
                WHERE id=?
            """, (
                klant_id,
                request.form.get("ophaal_datum") or today_iso(),
                tijd_type,
                tijd_van,
                tijd_tot,
                request.form.get("tijd_extra", "").strip(),
                int(request.form.get("soort1") or 0),
                int(request.form.get("soort2") or 0),
                int(request.form.get("dubbeldooiers") or 0),
                1 if request.form.get("factuur") == "on" else 0,
                1 if request.form.get("factuur_meegeven") == "on" else 0,
                1 if request.form.get("pinnen") == "on" else 0,
                1 if request.form.get("contant") == "on" else 0,
                1 if form_has_fixed_price(request.form) else 0,
                parse_money(request.form.get("vaste_prijs_per_ei"), 0),
                request.form.get("opmerking", "").strip(),
                verwerkt,
                voltooid_op,
                now,
                id
            ))
            persist_order_price(conn, id)
            notify_order_event(conn, notification_kind, id)
            return redirect(app_url("index"))

        bestelling = conn.execute("""
            SELECT b.*, k.naam AS klant_naam, k.telefoon, k.email, k.adres
            FROM bestellingen b
            JOIN klanten k ON k.id=b.klant_id
            WHERE b.id=?
        """, (id,)).fetchone()
        if bestelling:
            bestelling = decorate_order(bestelling)
        klanten = conn.execute("SELECT * FROM klanten WHERE actief=1 ORDER BY naam").fetchall()
    return render_template("edit.html", bestelling=bestelling, klanten=klanten, state_token="", body_class="page-static")


@app.route("/bestelling/<int:id>/toggle/verwerkt", methods=["POST"])
def toggle_verwerkt(id):
    with db() as conn:
        row = conn.execute("SELECT verwerkt FROM bestellingen WHERE id=?", (id,)).fetchone()
        if not row:
            wants_json = "application/json" in (request.headers.get("Accept") or "") or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            if wants_json:
                return jsonify({"ok": False}), 404
            return redirect(app_url("index"))
        new = 0 if row["verwerkt"] else 1
        now = now_iso()
        voltooid_op = now if new else ""
        conn.execute("UPDATE bestellingen SET verwerkt=?, voltooid_op=?, bijgewerkt_op=? WHERE id=?", (new, voltooid_op, now, id))
        notify_order_event(conn, "Bestelling voltooid" if new else "Bestelling gewijzigd", id)

    # Op de overzichtspagina gebruiken we bewust een gewone POST-formulierknop.
    # Dat werkt betrouwbaarder in de Home Assistant mobiele app / Ingress WebView dan onclick + fetch.
    wants_json = "application/json" in (request.headers.get("Accept") or "") or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if wants_json:
        return jsonify({"ok": True, "waarde": new})
    return redirect(app_url("index"))

@app.route("/bestelling/<int:id>/verwijderen", methods=["POST"])
def bestelling_verwijderen(id):
    with db() as conn:
        row = fetch_order_for_notification(conn, id)
        bericht = build_notification_payload("Bestelling verwijderd", row) if row else {}
        conn.execute("DELETE FROM bestellingen WHERE id=?", (id,))
        if bericht:
            send_ha_notification(bericht)
    return redirect(app_url("index"))

@app.route("/klanten")
def klanten():
    q = request.args.get("q", "").strip()
    show_hidden = request.args.get("verborgen") == "1"
    with db() as conn:
        active_clause = "1=1" if show_hidden else "actief=1"
        if q:
            rows = conn.execute(f"""
                SELECT * FROM klanten
                WHERE {active_clause} AND (naam LIKE ? OR telefoon LIKE ?)
                ORDER BY actief DESC, naam
            """, (f"%{q}%", f"%{q}%")).fetchall()
        else:
            rows = conn.execute(f"SELECT * FROM klanten WHERE {active_clause} ORDER BY actief DESC, naam").fetchall()
    return render_template("klanten.html", klanten=rows, q=q, show_hidden=show_hidden, state_token="", body_class="page-static")

@app.route("/klant/nieuw", methods=["GET", "POST"])
def klant_nieuw():
    with db() as conn:
        if request.method == "POST":
            naam = request.form.get("naam", "").strip()
            telefoon = request.form.get("telefoon", "").strip()
            email = request.form.get("email", "").strip()
            adres = request.form.get("adres", "").strip()
            if not naam:
                return redirect(app_url("klant_nieuw", error="Vul een naam in."))
            existing = conn.execute("SELECT id FROM klanten WHERE lower(naam)=lower(?)", (naam,)).fetchone()
            if existing:
                conn.execute("UPDATE klanten SET telefoon=?, email=?, adres=?, actief=1, bijgewerkt_op=? WHERE id=?", (telefoon, email, adres, now_iso(), existing["id"]))
                return redirect(app_url("klanten"))
            conn.execute("INSERT INTO klanten (naam, telefoon, email, adres, actief, bijgewerkt_op) VALUES (?, ?, ?, ?, 1, ?)", (naam, telefoon, email, adres, now_iso()))
            return redirect(app_url("klanten"))
    return render_template("klant_edit.html", klant=None, state_token="", body_class="page-static")

@app.route("/klant/<int:id>/bewerken", methods=["GET", "POST"])
def klant_bewerken(id):
    with db() as conn:
        if request.method == "POST":
            naam = request.form.get("naam", "").strip()
            telefoon = request.form.get("telefoon", "").strip()
            email = request.form.get("email", "").strip()
            adres = request.form.get("adres", "").strip()
            if naam:
                conn.execute("UPDATE klanten SET naam=?, telefoon=?, email=?, adres=?, actief=1, bijgewerkt_op=? WHERE id=?", (naam, telefoon, email, adres, now_iso(), id))
            return redirect(app_url("klanten"))
        klant = conn.execute("SELECT * FROM klanten WHERE id=?", (id,)).fetchone()
    return render_template("klant_edit.html", klant=klant, state_token="", body_class="page-static")

@app.route("/klant/<int:id>/verbergen", methods=["POST"])
def klant_verbergen(id):
    with db() as conn:
        conn.execute("UPDATE klanten SET actief=0, bijgewerkt_op=? WHERE id=?", (now_iso(), id))
    return redirect(app_url("klanten"))

@app.route("/klant/<int:id>/herstellen", methods=["POST"])
def klant_herstellen(id):
    with db() as conn:
        conn.execute("UPDATE klanten SET actief=1, bijgewerkt_op=? WHERE id=?", (now_iso(), id))
    return redirect(app_url("klanten", verborgen="1"))

@app.route("/api/state")
def api_state():
    with db() as conn:
        token = state_token(conn)
    return jsonify({"token": token})


@app.route("/api/klant/<int:klant_id>/laatste-bestelling")
def api_laatste_bestelling(klant_id):
    with db() as conn:
        row = conn.execute("""
            SELECT b.*, k.naam AS klant_naam
            FROM bestellingen b
            JOIN klanten k ON k.id = b.klant_id
            WHERE b.klant_id = ?
            ORDER BY b.ophaal_datum DESC, b.aangemaakt_op DESC, b.id DESC
            LIMIT 1
        """, (klant_id,)).fetchone()

    if not row:
        return jsonify({"ok": False, "message": "Geen vorige bestelling gevonden"})

    def safe_get(key, default=0):
        try:
            return row[key]
        except Exception:
            return default

    summary_parts = []
    if safe_get("soort1", 0):
        summary_parts.append(f"1e: {safe_get('soort1')}")
    if safe_get("soort2", 0):
        summary_parts.append(f"2e: {safe_get('soort2')}")
    if safe_get("dubbeldooiers", 0):
        summary_parts.append(f"Dubbel: {safe_get('dubbeldooiers')}")

    return jsonify({
        "ok": True,
        "klant_id": klant_id,
        "klant_naam": row["klant_naam"],
        "datum": safe_get("ophaal_datum", ""),
        "summary": " · ".join(summary_parts) if summary_parts else "Geen aantallen",
        "soort1": safe_get("soort1", 0),
        "soort2": safe_get("soort2", 0),
        "dubbeldooiers": safe_get("dubbeldooiers", 0),
        "factuur": bool(safe_get("factuur", 0)),
        "factuur_meegeven": bool(safe_get("factuur_meegeven", 0)),
        "pinnen": bool(safe_get("pinnen", 0)),
        "contant": bool(safe_get("contant", 0)),
        "vaste_prijs_actief": bool(safe_get("vaste_prijs_actief", 0)),
        "prijs_type": "vast" if bool(safe_get("vaste_prijs_actief", 0)) else "lijst",
        "vaste_prijs_per_ei": safe_get("vaste_prijs_per_ei", 0) or 0,
        "opmerking": safe_get("opmerking", "") or ""
    })


if __name__ == "__main__":
    port = int(os.environ.get("EIERAGENDA_PORT", "8099"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
