from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
import os
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date, datetime, timedelta

APP_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("EIERAGENDA_DB_PATH", APP_DIR / "eieragenda.db"))
APP_VERSION = "v47-addon-v10-webhook-schoon"
HA_NOTIFY_ENTITY = os.environ.get("EIERAGENDA_HA_NOTIFY_ENTITY", "input_text.eieragenda_notificatie")

app = Flask(__name__)


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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def today_iso():
    return date.today().isoformat()

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return date.today()

def pretty_date(value):
    d = parse_date(value)
    dagen = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
    maanden = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
    if d == date.today():
        prefix = "Vandaag"
    elif d == date.today() + timedelta(days=1):
        prefix = "Morgen"
    else:
        prefix = dagen[d.weekday()].capitalize()
    return f"{prefix} · {d.day} {maanden[d.month-1]}"


def pretty_datetime(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        if dt.date() == date.today():
            return "vandaag " + dt.strftime("%H:%M")
        if dt.date() == date.today() - timedelta(days=1):
            return "gisteren " + dt.strftime("%H:%M")
        return dt.strftime("%d-%m-%Y %H:%M")
    except Exception:
        return value

def init_db():
    with db() as conn:
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

@app.before_request
def before():
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


def notification_amounts(row):
    parts = []
    try:
        s1 = int(row["soort1"] or 0)
        s2 = int(row["soort2"] or 0)
        dd = int(row["dubbeldooiers"] or 0)
    except Exception:
        s1 = s2 = dd = 0
    if s1:
        parts.append(f"{s1} 1e soort")
    if s2:
        parts.append(f"{s2} 2e soort")
    if dd:
        parts.append(f"{dd} dubbeldooiers")
    return " + ".join(parts) if parts else "geen eieren"


def build_notification_message(kind, row):
    naam = (row["klant_naam"] or "Onbekende klant").strip()
    adres = (row["adres"] or "").strip()
    klant = f"{naam} - {adres}" if adres else naam
    aantallen = notification_amounts(row)
    datum = compact_date(row["ophaal_datum"])
    tijd = time_label(row)
    # Tijdstip achteraan zodat Home Assistant altijd een nieuwe state ziet, ook bij twee gelijke meldingen.
    msg = f"{kind} | {klant} | {aantallen} | {datum} | {tijd} | #{row['id']} | {datetime.now().strftime('%H:%M:%S')}"
    if len(msg) > 255:
        msg = msg[:252] + "..."
    return msg


def fetch_order_for_notification(conn, order_id):
    return conn.execute("""
        SELECT b.*, k.naam AS klant_naam, k.telefoon, k.email, k.adres
        FROM bestellingen b
        JOIN klanten k ON k.id = b.klant_id
        WHERE b.id=?
    """, (order_id,)).fetchone()


def post_ha_webhook(value):
    """Fallback zonder SUPERVISOR_TOKEN.

    Maak in Home Assistant een automation met webhook_id: eieragenda_notificatie.
    Die automation zet input_text.eieragenda_notificatie.
    """
    webhook_id = os.environ.get("EIERAGENDA_HA_WEBHOOK_ID", "eieragenda_notificatie")
    payload = json.dumps({
        "message": value,
        "entity_id": HA_NOTIFY_ENTITY,
    }).encode("utf-8")

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
            print(f"[Eieragenda] HA webhook notificatie verzonden via {url}: {value}", flush=True)
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


def send_ha_notification(value):
    # Alleen webhook gebruiken; geen SUPERVISOR_TOKEN of directe HA API meer.
    post_ha_webhook(value)

def notify_order_event(conn, kind, order_id):
    row = fetch_order_for_notification(conn, order_id)
    if not row:
        print(f"[Eieragenda] Geen bestelling gevonden voor notificatie: {order_id}", flush=True)
        return
    send_ha_notification(build_notification_message(kind, row))

def time_sort_sql():
    return """
    CASE
      WHEN b.tijd_type='none' THEN '00:00'
      WHEN b.tijd_type='exact' AND b.tijd_van != '' THEN b.tijd_van
      WHEN b.tijd_type='range' AND b.tijd_van != '' THEN b.tijd_van
      ELSE '00:01'
    END
    """

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
    return order

def build_groups(conn, history=False, q="", datum=""):
    q = (q or "").strip()
    datum = (datum or "").strip()

    if history:
        where = "1=1"
        params = []
        order_dir = "DESC"
    else:
        # Toon vandaag/toekomst, maar óók oude bestellingen die nog niet voltooid zijn.
        where = "(b.ophaal_datum >= ? OR b.verwerkt = 0)"
        params = [today_iso()]
        order_dir = "ASC"

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
        ORDER BY b.ophaal_datum {order_dir}, b.verwerkt ASC, {time_sort_sql()}, k.naam
    """, tuple(params)).fetchall()

    grouped = []
    current_date = None
    for r in rows:
        if r["ophaal_datum"] != current_date:
            current_date = r["ophaal_datum"]
            grouped.append({
                "datum": current_date,
                "datum_mooi": pretty_date(current_date),
                "is_overdue": parse_date(current_date) < date.today(),
                "orders": [],
                "remaining": {"soort1": 0, "soort2": 0, "dubbeldooiers": 0, "aantal": 0},
            })
        g = grouped[-1]
        g["orders"].append(decorate_order(r))
        if not r["verwerkt"]:
            for key in ["soort1", "soort2", "dubbeldooiers"]:
                g["remaining"][key] += r[key]
            g["remaining"]["aantal"] += 1

    for g in grouped:
        g["remaining"]["soort1_stack"] = amount_text(g["remaining"]["soort1"])
        g["remaining"]["soort2_stack"] = amount_text(g["remaining"]["soort2"])
        g["remaining"]["dubbeldooiers_stack"] = amount_text(g["remaining"]["dubbeldooiers"])
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
    return render_template("index.html", klanten=klanten, grouped=grouped, today=today_iso(), state_token=token, history=False)

@app.route("/geschiedenis")
def geschiedenis():
    q = request.args.get("q", "").strip()
    datum = request.args.get("datum", "").strip()
    with db() as conn:
        klanten = conn.execute("SELECT * FROM klanten WHERE actief=1 ORDER BY naam").fetchall()
        grouped = build_groups(conn, history=True, q=q, datum=datum)
        token = state_token(conn)
    return render_template("geschiedenis.html", klanten=klanten, grouped=grouped, today=today_iso(), state_token=token, history=True, q=q, datum=datum, body_class="page-static")


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
             soort1, soort2, dubbeldooiers, factuur, factuur_meegeven, pinnen, contant, opmerking, verwerkt, aangemaakt_op, bijgewerkt_op)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (
            klant_id, request.form.get("ophaal_datum") or today_iso(),
            tijd_type, tijd_van, tijd_tot, request.form.get("tijd_extra", "").strip(),
            int(request.form.get("soort1") or 0), int(request.form.get("soort2") or 0), int(request.form.get("dubbeldooiers") or 0),
            1 if request.form.get("factuur") == "on" else 0,
            1 if request.form.get("factuur_meegeven") == "on" else 0,
            1 if request.form.get("pinnen") == "on" else 0,
            1 if request.form.get("contant") == "on" else 0,
            request.form.get("opmerking", "").strip(), now, now
        ))
        new_order_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
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
                    soort1=?, soort2=?, dubbeldooiers=?, factuur=?, factuur_meegeven=?, pinnen=?, contant=?, opmerking=?,
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
                request.form.get("opmerking", "").strip(),
                verwerkt,
                voltooid_op,
                now,
                id
            ))
            notify_order_event(conn, notification_kind, id)
            return redirect(app_url("index"))

        bestelling = conn.execute("""
            SELECT b.*, k.naam AS klant_naam, k.telefoon, k.email, k.adres
            FROM bestellingen b
            JOIN klanten k ON k.id=b.klant_id
            WHERE b.id=?
        """, (id,)).fetchone()
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
        conn.execute("DELETE FROM bestellingen WHERE id=?", (id,))
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
        "opmerking": safe_get("opmerking", "") or ""
    })


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("EIERAGENDA_PORT", "8099"))
    app.run(host="0.0.0.0", port=port, debug=False)
