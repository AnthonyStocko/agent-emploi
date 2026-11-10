#!/usr/bin/env python3
"""Agent de recherche d'emploi — serveur web (bibliothèque standard uniquement).

En local :   python app.py   puis ouvrir http://127.0.0.1:8765
Sur Render : image Docker (Dockerfile), variables APP_PASSWORD et CLAUDE_CODE_OAUTH_TOKEN.
"""
import datetime
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import cvimport
import db
import hub
import llm
import sources

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
STATUSES = ["nouvelle", "a_postuler", "postulee", "entretien", "refus", "offre", "ecartee"]
LIST_COLS = ("id, source, title, company, location, contract, salary, url, published, score, "
             "score_reason, status, applied_at, created_at, "
             "cv_md IS NOT NULL AS has_docs, prep_md IS NOT NULL AS has_prep")


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def get_offer(oid):
    rows = db.query("SELECT * FROM offers WHERE id=?", (oid,))
    if not rows:
        raise ApiError("Annonce introuvable.", 404)
    return rows[0]


def import_backup(body):
    """Restaure une sauvegarde créée par /api/export (remplace les données actuelles)."""
    offers, interviews = body.get("offers"), body.get("interviews") or []
    if not isinstance(offers, list) or not isinstance(body.get("settings"), dict):
        raise ApiError("Fichier de sauvegarde invalide.")
    cols = {t: [r["name"] for r in db.query(f"PRAGMA table_info({t})")] for t in ("offers", "interviews")}
    with db._lock, db.connect() as con:
        con.execute("DELETE FROM offers")
        con.execute("DELETE FROM interviews")
        for table, rows in (("offers", offers), ("interviews", interviews)):
            for row in rows:
                keys = [k for k in cols[table] if isinstance(row, dict) and k in row]
                if keys:
                    con.execute(f"INSERT INTO {table}({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
                                [row[k] for k in keys])
    db.set_settings(body["settings"])
    return {"offers": len(offers)}


def api_get(path):
    if path == "/api/settings":
        values = db.get_settings()
        values["_secrets_set"] = [k for k in db.SECRET_KEYS if values.get(k)]
        values["_from_env"] = [k for k in db.ENV_KEYS if os.getenv(k.upper())]
        values["_auth"] = bool(os.getenv("APP_PASSWORD"))
        for k in db.SECRET_KEYS:  # les secrets ne repartent jamais vers le navigateur
            values.pop(k, None)
        return values
    if path == "/api/summary":
        cols = "id, title, company, location, score, status, url, applied_at"
        week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        return {
            "total": db.query("SELECT COUNT(*) AS n FROM offers WHERE status NOT IN ('ecartee', 'refus')")[0]["n"],
            "unscored": db.query("SELECT COUNT(*) AS n FROM offers WHERE score IS NULL")[0]["n"],
            "by_status": {r["status"]: r["n"] for r in
                          db.query("SELECT status, COUNT(*) AS n FROM offers GROUP BY status")},
            "top": db.query(f"SELECT {cols} FROM offers WHERE score >= 60 AND status IN "
                            "('nouvelle', 'a_postuler') ORDER BY score DESC, id DESC LIMIT 10"),
            "relances": db.query(f"SELECT {cols} FROM offers WHERE status = 'postulee' "
                                 "AND applied_at <= ? ORDER BY applied_at", (week_ago,)),
        }
    if path == "/api/export":
        return {"settings": {r["key"]: json.loads(r["value"]) for r in
                             db.query("SELECT key, value FROM settings")},
                "offers": db.query("SELECT * FROM offers"),
                "interviews": db.query("SELECT * FROM interviews")}
    if path == "/api/offers":
        return db.query(f"SELECT {LIST_COLS} FROM offers ORDER BY score IS NULL, score DESC, id DESC")
    m = re.fullmatch(r"/api/offers/(\d+)", path)
    if m:
        o = get_offer(int(m.group(1)))
        o["interviews"] = db.query(
            "SELECT id, created_at, feedback_md FROM interviews WHERE offer_id=? "
            "AND feedback_md IS NOT NULL ORDER BY id DESC", (o["id"],))
        return o
    raise ApiError("Route inconnue.", 404)


def api_post(path, body):
    s = db.get_settings()
    if path == "/api/settings":
        db.set_settings({k: v for k, v in body.items() if isinstance(k, str)
                         and not k.startswith("_") and not (k in db.SECRET_KEYS and not v)})
        return {"ok": True}
    if path == "/api/import":
        return import_backup(body)
    if path == "/api/collect":
        report = {}
        for name in body.get("sources") or list(sources.COLLECTORS):
            fn = sources.COLLECTORS.get(name)
            if not fn:
                continue
            try:
                found = fn(s)
                report[name] = {"found": len(found), "new": sum(db.add_offer(o) for o in found)}
            except (sources.SourceError, llm.LLMError, ValueError, KeyError) as e:
                report[name] = {"error": str(e)}
        return report
    if path == "/api/score":
        scored, left = agent.score_pending(s)
        return {"scored": scored, "left": left}
    if path == "/api/profile/import":
        files = body.get("files")
        result = cvimport.build_profile(s, files if isinstance(files, list) else [])
        hub.send_event("profil", f"Profil déduit de ses CV : {result.get('resume')}\n"
                                 f"Postes recherchés : {result.get('keywords')}")
        return result
    if path == "/api/offers/manual":
        o = sources.manual(body.get("text"))
        return {"new": db.add_offer(o)}
    m = re.fullmatch(r"/api/offers/(\d+)/(update|delete|generate|prep|interview)", path)
    if m:
        o, action = get_offer(int(m.group(1))), m.group(2)
        if action == "delete":
            db.execute("DELETE FROM offers WHERE id=?", (o["id"],))
            db.execute("DELETE FROM interviews WHERE offer_id=?", (o["id"],))
            return {"ok": True}
        if action == "update":
            if "status" in body:
                if body["status"] not in STATUSES:
                    raise ApiError("Statut inconnu.")
                applied = o["applied_at"]
                if body["status"] == "postulee" and o["status"] != "postulee":
                    applied = datetime.date.today().isoformat()
                db.execute("UPDATE offers SET status=?, applied_at=? WHERE id=?",
                           (body["status"], applied, o["id"]))
                if body["status"] in ("ecartee", "a_postuler", "postulee") and body["status"] != o["status"]:
                    label = {"ecartee": "a ÉCARTÉ", "a_postuler": "a RETENU (à postuler)",
                             "postulee": "a POSTULÉ à"}[body["status"]]
                    hub.send_event("decision_offre", (
                        f"Le candidat {label} cette offre.\nPoste : {o['title']}\nEntreprise : {o['company']}\n"
                        f"Lieu : {o['location']}\nContrat : {o['contract']}\nSalaire : {o['salary']}\n"
                        f"Score donné par l'agent : {o['score']} ({o['score_reason']})\n"
                        f"Notes du candidat : {o['notes']}\nExtrait : {(o['description'] or '')[:700]}"))
            if "notes" in body:
                db.execute("UPDATE offers SET notes=? WHERE id=?", (str(body["notes"]), o["id"]))
            for col in ("cv_md", "letter_md"):
                if col in body:
                    db.execute(f"UPDATE offers SET {col}=? WHERE id=?", (str(body[col]), o["id"]))
                    if o[col] and str(body[col]).strip() != o[col].strip():
                        kind = "CV" if col == "cv_md" else "lettre de motivation"
                        hub.send_event("correction_texte", (
                            f"Le candidat a corrigé à la main un texte ({kind}) rédigé par l'agent.\n\n"
                            f"AVANT :\n{o[col][:3500]}\n\nAPRÈS :\n{str(body[col])[:3500]}"))
            return {"ok": True}
        if action == "generate":
            return agent.generate_application(s, o)
        if action == "prep":
            return agent.generate_prep(s, o)
        return agent.start_interview(s, o)
    m = re.fullmatch(r"/api/interviews/(\d+)/feedback", path)
    if m:
        rows = db.query("SELECT * FROM interviews WHERE id=?", (int(m.group(1)),))
        if not rows:
            raise ApiError("Entretien introuvable.", 404)
        answers = [str(a) for a in body.get("answers") or []]
        return agent.interview_feedback(s, rows[0], get_offer(rows[0]["offer_id"]), answers)
    raise ApiError("Route inconnue.", 404)


# ---------- connexion (même principe que agent-perso) ----------
SESSION_COOKIE = "app_session"
LOGIN_MAX_FAILURES = 10          # par adresse IP, sur LOGIN_WINDOW_SECONDS
LOGIN_MAX_FAILURES_TOTAL = 30    # toutes adresses confondues
LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures = []
_login_lock = threading.Lock()


def _credentials():
    return os.getenv("APP_USERNAME", "admin"), os.getenv("APP_PASSWORD", "")


def _session_key():
    user, password = _credentials()
    secret = os.getenv("APP_SESSION_SECRET") or f"agent-emploi|{user}|{password}"
    return hashlib.sha256(secret.encode()).digest()


def session_days():
    return int(os.getenv("APP_SESSION_DAYS", "30"))


def make_session_token():
    expires = int(time.time() + session_days() * 86400)
    sig = hmac.new(_session_key(), f"session|{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def valid_session_token(token):
    expires, _, sig = (token or "").partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    expected = hmac.new(_session_key(), f"session|{expires}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def valid_credentials(user, password):
    expected_user, expected_password = _credentials()
    return (secrets.compare_digest(user.encode(), expected_user.encode())
            & secrets.compare_digest(password.encode(), expected_password.encode()))


def _too_many_failures(ip):
    with _login_lock:
        now = time.monotonic()
        _login_failures[:] = [(t, a) for t, a in _login_failures if now - t < LOGIN_WINDOW_SECONDS]
        return (len(_login_failures) >= LOGIN_MAX_FAILURES_TOTAL
                or sum(1 for _, a in _login_failures if a == ip) >= LOGIN_MAX_FAILURES)


def login_page(error=""):
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Connexion — Agent emploi</title>
<style>body{{font:16px system-ui,sans-serif;background:#f5f6f8;display:grid;place-items:center;min-height:100vh;margin:0}}
form{{background:#fff;padding:24px;border-radius:12px;border:1px solid #e2e5ea;width:min(340px,90vw)}}
input,button{{font:inherit;width:100%;padding:10px;margin:6px 0 12px;border-radius:8px;border:1px solid #cfd4dc;box-sizing:border-box}}
button{{background:#2457d6;color:#fff;border-color:#2457d6;cursor:pointer}}.err{{color:#b42318}}h1{{font-size:20px;margin-top:0}}</style>
</head><body><form method="post" action="/login"><h1>Agent emploi</h1>{err}
<label>Identifiant<input name="username" autocomplete="username" required></label>
<label>Mot de passe<input name="password" type="password" autocomplete="current-password" required></label>
<button>Se connecter</button></form></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json; charset=utf-8", headers=()):
        data = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _cookie(self, value, max_age):
        secure = "; Secure" if os.getenv("RENDER") else ""
        return ("Set-Cookie", f"{SESSION_COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax; "
                              f"Max-Age={max_age}{secure}")

    def _client_ip(self):
        # Derrière le proxy de Render : la dernière adresse ajoutée est celle vue par le proxy
        parts = [p.strip() for p in (self.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
        return parts[-1] if parts else self.client_address[0]

    def _session(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return valid_session_token(value)
        return False

    def _guard(self, path):
        """Vrai si la requête peut continuer ; sinon la réponse est déjà envoyée."""
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin")
        # Une requête d'écriture doit venir de l'appli elle-même, pas d'un autre site
        if origin and urllib.parse.urlsplit(origin).netloc != host:
            self._send(403, {"error": "Origine refusée."})
            return False
        # L'agent chef s'identifie avec le jeton partagé HUB_TOKEN (routes /api/ uniquement)
        token = os.getenv("HUB_TOKEN") or ""
        given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if token and path.startswith("/api/") and secrets.compare_digest(given.encode(), token.encode()):
            return True
        if not os.getenv("APP_PASSWORD"):
            if os.getenv("RENDER"):
                self._send(503, {"error": "APP_PASSWORD n'est pas défini dans les variables "
                                          "d'environnement Render."})
                return False
            if host.split(":")[0] not in ("127.0.0.1", "localhost"):
                self._send(403, {"error": "Accès local uniquement (définis APP_PASSWORD pour "
                                          "un accès distant)."})
                return False
            return True  # en local sans mot de passe : accès libre
        if path == "/login" or self._session():
            return True
        if self.command == "GET" and not path.startswith("/api/"):
            self._send(303, b"", "text/plain", [("Location", "/login")])
        else:
            self._send(401, {"error": "Connexion expirée : recharge la page pour te reconnecter."})
        return False

    def _login(self):
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        ip = self._client_ip()
        if _too_many_failures(ip):
            return self._send(429, login_page("Trop de tentatives. Réessaie dans 15 minutes."), "text/html; charset=utf-8")
        if valid_credentials(form.get("username", [""])[0], form.get("password", [""])[0]):
            return self._send(303, b"", "text/plain", [
                ("Location", "/"), self._cookie(make_session_token(), session_days() * 86400)])
        with _login_lock:
            _login_failures.append((time.monotonic(), ip))
        self._send(401, login_page("Identifiant ou mot de passe incorrect."), "text/html; charset=utf-8")

    def _api(self, fn, *args):
        try:
            self._send(200, fn(*args))
        except ApiError as e:
            self._send(e.code, {"error": str(e)})
        except (llm.LLMError, sources.SourceError, cvimport.ImportError_) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # dernière barrière : l'appli ne doit pas tomber
            print(f"Erreur interne : {e!r}", flush=True)
            self._send(500, {"error": f"Erreur interne : {e}"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self._send(200, {"ok": True})
        if not self._guard(path):
            return
        if path == "/login":
            if not os.getenv("APP_PASSWORD"):
                return self._send(303, b"", "text/plain", [("Location", "/")])
            return self._send(200, login_page(), "text/html; charset=utf-8")
        if path.startswith("/api/"):
            return self._api(api_get, path)
        name = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, name))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "Introuvable."})
        with open(full, "rb") as f:
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read(), ctype)

    def do_HEAD(self):  # sonde de Render
        self._send(200, b"", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._guard(path):
            return
        if path == "/login":
            return self._login()
        if path == "/logout":
            return self._send(303, b"", "text/plain", [("Location", "/login"), self._cookie("", 0)])
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 30_000_000:
                raise ValueError
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "Requête invalide."})
        self._api(api_post, path, body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.getenv("PORT") or 8765)
    # Render (ou HOST=0.0.0.0) : écoute sur toutes les interfaces ; sinon, ton poste uniquement
    host = os.getenv("HOST") or ("0.0.0.0" if os.getenv("RENDER") else "127.0.0.1")
    db.init()
    print(f"Agent emploi prêt : http://{host}:{port}  (Ctrl+C pour arrêter)", flush=True)
    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
