"""Stockage SQLite local (aucune donnée ne quitte ta machine, hors appels aux API)."""
import json
import os
import sqlite3
import threading

# DATA_DIR déplace la base (disque persistant Render, par exemple) ; par défaut, à côté du code.
DATA_DIR = os.getenv("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "data.sqlite3")
# Réglages qui peuvent venir des variables d'environnement (persistantes sur Render, prioritaires sur
# la base) : FT_CLIENT_ID, FT_CLIENT_SECRET, ADZUNA_APP_ID, ADZUNA_APP_KEY, IMAP_HOST, IMAP_USER, ...
ENV_KEYS = ["ft_client_id", "ft_client_secret", "adzuna_app_id", "adzuna_app_key",
            "imap_host", "imap_user", "imap_password", "imap_senders", "imap_days"]
SECRET_KEYS = {"ft_client_secret", "adzuna_app_key", "imap_password"}
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS offers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, ext_id TEXT, title TEXT, company TEXT, location TEXT,
  contract TEXT, salary TEXT, url TEXT, description TEXT, published TEXT,
  score INTEGER, score_reason TEXT,
  status TEXT DEFAULT 'nouvelle', applied_at TEXT, notes TEXT DEFAULT '',
  cv_md TEXT, letter_md TEXT, prep_md TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(source, ext_id)
);
CREATE TABLE IF NOT EXISTS interviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  offer_id INTEGER, questions TEXT, answers TEXT, feedback_md TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS seen_mails (msg_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS selections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT, offer_ids TEXT, motif_si_vide TEXT, report TEXT, report_md TEXT
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  texte TEXT, options TEXT, reponse TEXT, date_reponse TEXT,
  date_pose TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cv_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fichier TEXT, poste_vise TEXT, titre TEXT, competences TEXT, date TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  etape TEXT, modele TEXT, taille_prompt INTEGER, taille_reponse INTEGER, duree_ms INTEGER, ok INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
"""
# Colonnes ajoutées après coup : créées au démarrage si la base est plus ancienne
OFFER_COLUMNS = {"selected_at": "TEXT", "selection_md": "TEXT"}


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        # Première version de la table des questions (colonnes en anglais) : renommées en place
        qcols = {r[1] for r in con.execute("PRAGMA table_info(questions)")}
        for old, new in (("text", "texte"), ("answer", "reponse"), ("answered_at", "date_reponse"),
                         ("asked_at", "date_pose")):
            if old in qcols:
                con.execute(f"ALTER TABLE questions RENAME COLUMN {old} TO {new}")
        have = {r[1] for r in con.execute("PRAGMA table_info(offers)")}
        for col, kind in OFFER_COLUMNS.items():
            if col not in have:
                con.execute(f"ALTER TABLE offers ADD COLUMN {col} {kind}")


def query(sql, params=()):
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with _lock, connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid if cur.lastrowid else cur.rowcount


def get_settings():
    values = {r["key"]: json.loads(r["value"]) for r in query("SELECT key, value FROM settings")}
    for key in ENV_KEYS:
        if os.getenv(key.upper()):
            values[key] = os.getenv(key.upper())
    return values


def set_settings(values):
    for k, v in values.items():
        execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, json.dumps(v)))


def add_offer(o):
    """Insère une annonce ; renvoie True si elle est nouvelle."""
    with _lock, connect() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO offers(source, ext_id, title, company, location, contract, "
            "salary, url, description, published) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (o.get("source"), str(o.get("ext_id")), o.get("title") or "(sans titre)",
             o.get("company") or "", o.get("location") or "", o.get("contract") or "",
             o.get("salary") or "", o.get("url") or "", o.get("description") or "",
             o.get("published") or ""))
        return cur.rowcount > 0
