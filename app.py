#!/usr/bin/env python3
"""Agent de recherche d'emploi — serveur web local (bibliothèque standard uniquement).

Lancement :  python app.py   puis ouvrir http://127.0.0.1:8765
"""
import datetime
import json
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import db
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


def api_get(path):
    if path == "/api/settings":
        return db.get_settings()
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
        db.set_settings({k: v for k, v in body.items() if isinstance(k, str)})
        return {"ok": True}
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
        return {"scored": agent.score_pending(s)}
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
            if "notes" in body:
                db.execute("UPDATE offers SET notes=? WHERE id=?", (str(body["notes"]), o["id"]))
            for col in ("cv_md", "letter_md"):
                if col in body:
                    db.execute(f"UPDATE offers SET {col}=? WHERE id=?", (str(body[col]), o["id"]))
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


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _local_only(self):
        """Refuse les requêtes venant d'un autre site (protection des données locales)."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        ok = host in ("127.0.0.1", "localhost") and (
            not origin or re.fullmatch(r"https?://(127\.0\.0\.1|localhost)(:\d+)?", origin))
        if not ok:
            self._send(403, {"error": "Accès local uniquement."})
        return ok

    def _api(self, fn, *args):
        try:
            self._send(200, fn(*args))
        except ApiError as e:
            self._send(e.code, {"error": str(e)})
        except (llm.LLMError, sources.SourceError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # dernière barrière : l'appli ne doit pas tomber
            self._send(500, {"error": f"Erreur interne : {e}"})

    def do_GET(self):
        if not self._local_only():
            return
        path = self.path.split("?")[0]
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

    def do_POST(self):
        if not self._local_only():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "Requête invalide."})
        self._api(api_post, self.path.split("?")[0], body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    db.init()
    print(f"Agent emploi prêt : http://127.0.0.1:{port}  (Ctrl+C pour arrêter)")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
