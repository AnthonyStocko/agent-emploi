"""Lien avec l'agent chef (facultatif) : lire la mémoire commune et lui envoyer des événements.

Actif seulement si HUB_URL et HUB_TOKEN sont définis. Toute panne de l'agent chef est sans
conséquence : l'agent emploi continue de fonctionner, simplement sans mémoire.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HUB_URL = (os.getenv("HUB_URL") or "").rstrip("/")
HUB_TOKEN = (os.getenv("HUB_TOKEN") or "").strip()
_cache = {}


def enabled():
    return bool(HUB_URL and HUB_TOKEN)


def _call(path, body=None, timeout=45):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HUB_URL + path, data=data, headers={
        "Authorization": "Bearer " + HUB_TOKEN, "Content-Type": "application/json",
        "User-Agent": "agent-emploi/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


def memory_text(*categories):
    """Souvenirs utiles pour un prompt (texte, vide si l'agent chef est absent ou injoignable)."""
    if not enabled():
        return ""
    key = ",".join(categories)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < hit[2]:
        return hit[1]
    try:
        text = str(_call("/agent/memory?" + urllib.parse.urlencode({"categories": key})).get("text") or "")
        _cache[key] = (time.time(), text, 300)   # gardé 5 minutes
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"Agent chef injoignable (mémoire ignorée) : {e}", flush=True)
        text = ""
        _cache[key] = (time.time(), text, 60)    # on réessaie dans une minute
    return text


def send_event(kind, content):
    """Signale à l'agent chef quelque chose dont il peut tirer un souvenir (en arrière-plan)."""
    if not enabled() or not str(content or "").strip():
        return

    def push():
        try:
            _call("/agent/events", {"agent": "emploi", "kind": kind, "content": str(content)[:8000]}, 90)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"Agent chef injoignable (événement {kind} perdu) : {e}", flush=True)

    threading.Thread(target=push, daemon=True).start()
