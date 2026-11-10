"""Collecte des annonces : France Travail, Adzuna, alertes mail, saisie manuelle."""
import datetime
import email
import hashlib
import html
import imaplib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from email.header import decode_header, make_header

import db
import llm

UA = {"User-Agent": "agent-emploi/1.0 (usage personnel)"}


class SourceError(Exception):
    pass


def _http(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise SourceError(f"HTTP {e.code} : {body}")
    except urllib.error.URLError as e:
        raise SourceError(f"Connexion impossible : {e.reason}")


def html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", raw)
    raw = re.sub(r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", raw)
    raw = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


def keyword_list(s):
    """Postes à rechercher : un par ligne ou séparés par des virgules (4 recherches au maximum)."""
    items = [k.strip() for k in re.split(r"[,;\n]+", s.get("keywords") or "") if k.strip()]
    return items[:4] or [""]


# ---------- France Travail ----------
def france_travail(s):
    cid, secret = s.get("ft_client_id"), s.get("ft_client_secret")
    if not (cid and secret):
        raise SourceError("Identifiants France Travail manquants (onglet Réglages).")
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": cid, "client_secret": secret,
        "scope": "api_offresdemploiv2 o2dsoffre"}).encode()
    tok = json.loads(_http(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire",
        data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}))
    out, seen = [], set()
    for kw in keyword_list(s):
        params = {"motsCles": kw, "range": "0-29"}
        if s.get("departement"):
            params["departement"] = s["departement"]
        raw = _http("https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search?"
                    + urllib.parse.urlencode(params),
                    headers={"Authorization": "Bearer " + tok["access_token"],
                             "Accept": "application/json"})
        for r in (json.loads(raw).get("resultats", []) if raw.strip() else []):
            if r.get("id") in seen:
                continue
            seen.add(r.get("id"))
            out.append({
                "source": "France Travail", "ext_id": r.get("id"), "title": r.get("intitule"),
                "company": (r.get("entreprise") or {}).get("nom"),
                "location": (r.get("lieuTravail") or {}).get("libelle"),
                "contract": r.get("typeContratLibelle") or r.get("typeContrat"),
                "salary": (r.get("salaire") or {}).get("libelle"),
                "url": (r.get("origineOffre") or {}).get("urlOrigine")
                       or f"https://candidat.francetravail.fr/offres/recherche/detail/{r.get('id')}",
                "description": r.get("description"), "published": r.get("dateCreation")})
    return out


# ---------- Adzuna ----------
def adzuna(s):
    app_id, key = s.get("adzuna_app_id"), s.get("adzuna_app_key")
    if not (app_id and key):
        raise SourceError("Identifiants Adzuna manquants (onglet Réglages).")
    out, seen = [], set()
    for kw in keyword_list(s):
        params = {"app_id": app_id, "app_key": key, "results_per_page": 30,
                  "what": kw, "sort_by": "date", "content-type": "application/json"}
        if s.get("location"):
            params["where"] = s["location"]
        raw = _http("https://api.adzuna.com/v1/api/jobs/fr/search/1?" + urllib.parse.urlencode(params))
        for r in json.loads(raw).get("results", []):
            if r.get("id") in seen:
                continue
            seen.add(r.get("id"))
            lo, hi = r.get("salary_min"), r.get("salary_max")
            salary = f"{int(lo)} – {int(hi)} €/an" if lo and hi else (f"{int(lo)} €/an" if lo else "")
            out.append({
                "source": "Adzuna", "ext_id": r.get("id"), "title": html_to_text(r.get("title") or ""),
                "company": (r.get("company") or {}).get("display_name"),
                "location": (r.get("location") or {}).get("display_name"),
                "contract": r.get("contract_type"), "salary": salary, "url": r.get("redirect_url"),
                "description": html_to_text(r.get("description") or ""), "published": r.get("created")})
    return out


# ---------- Alertes mail (IMAP) ----------
def _mail_text(msg):
    plain, rich = "", ""
    for part in msg.walk():
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_subtype() == "html":
            rich += text
        else:
            plain += text
    return html_to_text(rich) if rich else plain


def mail_alerts(s):
    host, user, pwd = s.get("imap_host"), s.get("imap_user"), s.get("imap_password")
    if not (host and user and pwd):
        raise SourceError("Réglages IMAP manquants (onglet Réglages).")
    senders = [x.strip().lower() for x in (s.get("imap_senders") or "").split(",") if x.strip()]
    if not senders:
        raise SourceError("Indique au moins un expéditeur d'alertes (ex. linkedin.com).")
    since = (datetime.date.today() - datetime.timedelta(days=int(s.get("imap_days") or 3)))
    seen = {r["msg_id"] for r in db.query("SELECT msg_id FROM seen_mails")}
    out = []
    try:
        box = imaplib.IMAP4_SSL(host)
        box.login(user, pwd)
    except (imaplib.IMAP4.error, OSError) as e:
        raise SourceError(f"Connexion IMAP refusée : {e}")
    try:
        box.select("INBOX", readonly=True)
        _, data = box.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        for num in (data[0].split() if data and data[0] else [])[-60:]:
            _, parts = box.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(parts[0][1])
            sender = str(make_header(decode_header(msg.get("From", "")))).lower()
            msg_id = msg.get("Message-ID") or hashlib.sha1(parts[0][1]).hexdigest()
            if msg_id in seen or not any(x in sender for x in senders):
                continue
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            body = _mail_text(msg)[:9000]
            jobs = llm.ask_json(
                "Voici un mail d'alerte emploi. Extrais chaque annonce qu'il contient.\n"
                "Format : liste JSON d'objets {\"title\", \"company\", \"location\", \"url\", "
                "\"description\"}. N'invente rien : laisse une chaîne vide si l'info manque. "
                "Renvoie [] si le mail ne contient aucune annonce.\n\n"
                f"Objet : {subject}\n\n{body}")
            for j in jobs if isinstance(jobs, list) else []:
                if not isinstance(j, dict) or not j.get("title"):
                    continue
                key = j.get("url") or (j.get("title", "") + "|" + j.get("company", ""))
                out.append({"source": "Alerte mail", "title": j.get("title"),
                            "ext_id": hashlib.sha1(key.encode()).hexdigest()[:16],
                            "company": j.get("company"), "location": j.get("location"),
                            "url": j.get("url"), "description": j.get("description"),
                            "published": msg.get("Date", "")})
            db.execute("INSERT OR IGNORE INTO seen_mails(msg_id) VALUES(?)", (msg_id,))
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return out


# ---------- Annonce collée à la main ----------
def manual(text):
    text = (text or "").strip()
    if not text:
        raise SourceError("Colle le texte ou le lien d'une annonce.")
    url = ""
    if re.fullmatch(r"https?://\S+", text):
        url = text
        text = html_to_text(_http(url))[:15000]
        if len(text) < 200:
            raise SourceError("Page illisible (site protégé ?). Colle plutôt le texte de l'annonce.")
    j = llm.ask_json(
        "Structure cette annonce d'emploi en JSON : {\"title\", \"company\", \"location\", "
        "\"contract\", \"salary\", \"description\"}. La description reprend fidèlement missions, "
        "profil recherché et conditions. N'invente rien : chaîne vide si l'info manque.\n\n" + text)
    if not isinstance(j, dict):
        raise SourceError("Annonce non reconnue.")
    j.update(source="Manuelle", url=url,
             ext_id=hashlib.sha1((url or text).encode()).hexdigest()[:16],
             description=j.get("description") or text)
    return j


COLLECTORS = {"france_travail": france_travail, "adzuna": adzuna, "mail": mail_alerts}
