"""Base durable sans disque : une copie de la base est gardée chez l'agent chef.

Sur l'offre gratuite de Render, le fichier data.sqlite3 est effacé à chaque redéploiement et à chaque mise
en veille du service. Avec HUB_URL et HUB_TOKEN :
- au démarrage, si la base locale est vide, l'agent reprend la dernière copie (GET /agent/coffre) ;
- après chaque écriture, il en dépose une nouvelle copie, regroupée sur quelques secondes (POST).

La copie contient le CV maître, les réglages, les annonces, les questions et l'archive de CV, mais jamais
les secrets (identifiants des sources, mot de passe de la boîte mail) : ceux-là restent dans les variables
d'environnement Render. Tant que la reprise n'a pas réussi, aucune copie n'est déposée, pour ne jamais
écraser la copie de l'agent chef avec une base vide.
"""
import base64
import gzip
import json
import threading
import time
import urllib.error

import db
import hub

TABLES = ["offers", "interviews", "questions", "cv_archive", "selections", "seen_mails", "llm_calls"]
DELAI = 8                 # secondes de regroupement des écritures avant un dépôt
VERSION = 1

state = {"ready": False, "restoring": False, "local_data": None, "last_saved": None, "error": ""}
_timer = None
_timer_lock = threading.Lock()
_save_lock = threading.Lock()


def enabled():
    return hub.enabled()


def is_empty():
    s = db.get_settings()
    return not (s.get("cv_master") or "").strip() and not db.query("SELECT 1 FROM offers LIMIT 1")


def snapshot():
    settings = {r["key"]: json.loads(r["value"]) for r in db.query("SELECT key, value FROM settings")}
    for k in db.SECRET_KEYS:
        settings.pop(k, None)
    data = {"version": VERSION, "settings": settings,
            "tables": {t: db.query(f"SELECT * FROM {t}") for t in TABLES}}
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def load(contenu):
    """Remplace la base locale par une copie (les secrets locaux sont conservés)."""
    data = json.loads(gzip.decompress(base64.b64decode(contenu)).decode("utf-8"))
    if data.get("version") != VERSION:
        raise ValueError("Copie d'une autre version.")
    cols = {t: [r["name"] for r in db.query(f"PRAGMA table_info({t})")] for t in TABLES}
    with db._lock, db.connect() as con:
        for t in TABLES:
            rows = (data.get("tables") or {}).get(t)
            if not isinstance(rows, list):
                continue
            con.execute(f"DELETE FROM {t}")
            for row in rows:
                keys = [k for k in cols[t] if k in row]
                if keys:
                    con.execute(f"INSERT INTO {t}({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
                                [row[k] for k in keys])
        for k, v in (data.get("settings") or {}).items():
            if k not in db.SECRET_KEYS:
                con.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, json.dumps(v)))
    return {t: len((data.get("tables") or {}).get(t) or []) for t in TABLES}


def restore(attempts=1, pause=20):
    """Reprend la copie de l'agent chef si la base locale est vide. Vrai quand l'agent peut déposer."""
    if not enabled():
        return False
    if state["ready"]:
        return True
    # Base locale déjà remplie au démarrage (en local, ou disque persistant) : elle fait foi. Constaté une
    # seule fois : après une reprise ratée, une base remplie depuis n'écrase pas la copie de l'agent chef.
    if state.get("local_data") is None:
        state["local_data"] = not is_empty()
    if state["local_data"]:
        state["ready"] = True
        return True
    state["restoring"] = True
    try:
        for i in range(attempts):
            try:
                r = hub._call("/agent/coffre?agent=emploi", timeout=90)
                if r.get("contenu"):
                    counts = load(r["contenu"])
                    print(f"Base reprise de l'agent chef ({r.get('updated_at')}) : {counts}", flush=True)
                state.update(ready=True, error="")
                return True
            except (urllib.error.URLError, OSError, ValueError) as e:
                state["error"] = str(e)[:200]
                print(f"Reprise de la base impossible (essai {i + 1}/{attempts}) : {e}", flush=True)
                if i + 1 < attempts:
                    time.sleep(pause)
        return False
    finally:
        state["restoring"] = False


def save():
    """Dépose une copie de la base chez l'agent chef (jamais avant une reprise réussie)."""
    if not enabled():
        return False
    with _save_lock:
        if not state["ready"] and not restore():
            return False
        try:
            hub._call("/agent/coffre", {"agent": "emploi", "contenu": snapshot(), "vide": is_empty()}, timeout=90)
            state.update(last_saved=time.time(), error="")
            return True
        except (urllib.error.URLError, OSError, ValueError) as e:
            state["error"] = str(e)[:200]
            print(f"Copie de la base non déposée : {e}", flush=True)
            schedule(60)   # nouvel essai dans une minute
            return False


def schedule(delay=DELAI):
    """Programme un dépôt ; les écritures rapprochées n'en font qu'un."""
    global _timer
    if not enabled():
        return
    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(delay, save)
        _timer.daemon = True
        _timer.start()


def start():
    """Au démarrage : reprise en arrière-plan (l'agent chef peut mettre une minute à se réveiller), puis
    dépôt après chaque écriture."""
    if not enabled():
        return
    db.listeners.append(schedule)
    state["local_data"] = not is_empty()
    state["restoring"] = True   # les routes /api/ répondent 503 jusqu'à la fin de la reprise
    threading.Thread(target=restore, kwargs={"attempts": 6}, daemon=True).start()
