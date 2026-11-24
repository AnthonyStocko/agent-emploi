"""La sélection : 1 ou 2 offres très ciblées avec CV et lettre, et 1 ou 2 questions fermées.

L'agent chef la lance les lundis, mercredis et vendredis (POST /api/selection) ; les autres jours, il ne
fait que collecter. Étapes (voir MISSION dans agent.py) : collecte, préfiltre sans modèle, une passe de
notation, un appel de sélection, au plus 2 rédactions (sautées au-delà du budget du cycle), un appel pour
les questions. Tout reste dans la base de l'agent.
"""
import datetime
import json
import re
import threading
import unicodedata

import agent
import db
import hub
import llm
import sources

SOURCE_NAMES = {"france_travail": "France Travail", "adzuna": "Adzuna", "mail": "Alertes mail"}
SCORE_LIMIT = 24       # annonces notées par cycle, en un seul appel
SELECT_MIN = 80
SELECT_MAX = 2
QUESTIONS_PER_DAY = 2
_running = threading.Lock()


def _utcnow():
    """Heure UTC sans fuseau, comme datetime('now') de SQLite."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _sql_time(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------- préfiltre sans modèle ----------
def _norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def _terms(value):
    if isinstance(value, list):
        value = ",".join(str(v) for v in value)
    return [_norm(t) for t in re.split(r"[,;\n]", str(value or "")) if _norm(t)]


def contract_kind(text):
    """Range un intitulé de contrat (France Travail, Adzuna, saisie libre) dans une famille."""
    c = _norm(text)
    if not c:
        return ""
    if "indetermine" in c or re.search(r"\bcdi\b", c) or "permanent" in c:
        return "cdi"
    if "determine" in c or re.search(r"\bcdd\b", c) or c == "contract":
        return "cdd"
    if "interim" in c or re.search(r"\bmis\b", c) or "temporaire" in c:
        return "interim"
    if "freelance" in c or "independant" in c or "liberal" in c:
        return "freelance"
    if "alternance" in c or "apprenti" in c or "professionnalisation" in c:
        return "alternance"
    if "stage" in c:
        return "stage"
    return "autre"


def _in_zone(location, zones):
    loc = _norm(location)
    if not zones or not loc:
        return True  # lieu inconnu : on laisse la notation trancher
    for z in zones:
        if z.isdigit():  # département : « 69 - Lyon », « 69003 Lyon »
            if re.search(rf"(^|\D){z}(\D|$)", loc) or re.search(rf"(^|\D){z}\d{{3}}(\D|$)", loc):
                return True
        elif re.search(rf"\b{re.escape(z)}\b", loc):
            return True
    return False


def zones_of(s):
    """Zone acceptée : le réglage « zone » ; à défaut, le département et la ville du profil (ceux qui
    servent déjà à la recherche), pour qu'un profil rempli ne laisse jamais passer toute la France."""
    zones = _terms(s.get("zone"))
    if zones:
        return zones
    return _terms(s.get("departement")) + _terms(s.get("location"))


def zone_text(s):
    zones = zones_of(s)
    return ", ".join(zones) if zones else "(aucune zone réglée)"


def prefilter_reason(o, s, seen=None):
    """Raison d'écarter l'annonce sans modèle, ou chaîne vide si elle passe."""
    if seen is not None:
        # Même intitulé et même entreprise qu'une annonce déjà connue (vue, écartée ou postulée)
        key = (_norm(o.get("title")), _norm(o.get("company")))
        if key[1] and key in seen and seen[key] != o["id"]:
            return f"déjà vue (annonce n°{seen[key]})"
    zones = zones_of(s)
    if not _in_zone(o.get("location"), zones):
        return f"hors zone ({o.get('location')})"
    wanted = _terms(s.get("contracts"))
    kind = contract_kind(o.get("contract"))
    if wanted and kind:
        ok_kinds = {contract_kind(w) for w in wanted}
        if kind not in ok_kinds and not (kind == "autre" and any(w in _norm(o.get("contract")) for w in wanted)):
            return f"type de contrat exclu ({o.get('contract')})"
    text = _norm(" ".join(str(o.get(k) or "") for k in ("title", "company", "description")))
    for word in _terms(s.get("exclude_words")):
        if re.search(rf"\b{re.escape(word)}\b", text):
            return f"mot exclu « {word} »"
    return ""


def prefilter(s):
    """Écarte, avant toute notation, les annonces déjà vues (titre + entreprise), hors zone, hors
    contrat ou contenant un mot exclu."""
    seen = {}
    for r in db.query("SELECT id, title, company FROM offers ORDER BY id"):
        seen.setdefault((_norm(r["title"]), _norm(r["company"])), r["id"])
    out = 0
    for o in db.query("SELECT * FROM offers WHERE score IS NULL ORDER BY id"):
        reason = prefilter_reason(o, s, seen)
        if reason:
            db.execute("UPDATE offers SET score=0, score_reason=?, status='ecartee' WHERE id=?",
                       ("Préfiltre : " + reason, o["id"]))
            out += 1
    return out


# ---------- notation, sélection, questions ----------
def score(s, ids):
    """Une seule passe de notation, au modèle économique. Les réponses aux questions sont dans le profil."""
    if not ids:
        return 0
    rows = db.query(f"SELECT * FROM offers WHERE id IN ({','.join('?' * len(ids))})", ids)
    listing = "\n\n".join(f"### ANNONCE id={o['id']}\n{agent._offer_text(o, 1500)}" for o in rows)
    res = llm.ask_json(
        f"{agent.MISSION}\n\n{agent._profile(s, 'gouts_offres', 'profil')}\n\nÉTAPE 3 (notation). Note de 0 "
        "à 100 l'adéquation de chaque annonce avec le CV maître et les goûts connus. Sois exigeant : 80 ou "
        "plus seulement si les exigences clés sont couvertes par le CV maître.\n"
        f"Zone géographique acceptée : {zone_text(s)}. Une annonce dont le lieu est hors de cette zone est notée "
        "moins de 50 ; si le lieu n'apparaît ni dans le champ Lieu ni dans le texte, dis-le dans la raison et ne "
        "dépasse pas 79.\n"
        "Format : liste JSON d'objets "
        "{\"id\": entier, \"score\": 0-100, \"raison\": \"une phrase : point fort principal, puis écart "
        "principal\"}.\n\n" + listing, model=llm.ECO_MODEL, step="notation")
    done = 0
    for r in res if isinstance(res, list) else []:
        try:
            oid, sc = int(r["id"]), max(0, min(100, int(r["score"])))
        except (KeyError, TypeError, ValueError):
            continue
        if oid in ids:
            db.execute("UPDATE offers SET score=?, score_reason=? WHERE id=?", (sc, str(r.get("raison", "")), oid))
            done += 1
    return done


def _decisions_text():
    rows = db.query("SELECT title, company, status FROM offers WHERE status IN ('ecartee', 'a_postuler', "
                    "'postulee', 'entretien', 'offre') AND COALESCE(score_reason, '') NOT LIKE 'Préfiltre%' "
                    "ORDER BY id DESC LIMIT 15")
    label = {"ecartee": "écartée", "a_postuler": "retenue", "postulee": "postulée", "entretien": "entretien",
             "offre": "offre reçue"}
    return "\n".join(f"- {r['title']} — {r['company'] or '?'} : {label[r['status']]}" for r in rows)


def select(s, scored_ids):
    """Un appel de sélection : au plus 2 offres notées 80 ou plus, ou l'explication de l'absence."""
    since = _sql_time(_utcnow() - datetime.timedelta(days=30))
    candidates = []
    for o in db.query("SELECT * FROM offers WHERE score >= ? AND status = 'nouvelle' AND selected_at IS NULL "
                      "AND created_at >= ? ORDER BY score DESC, id DESC", (SELECT_MIN, since)):
        # Une offre notée avant le préfiltre, ou avant un changement de réglage, repasse le préfiltre ici
        reason = prefilter_reason(o, s)
        if reason:
            db.execute("UPDATE offers SET status='ecartee', score_reason=? WHERE id=?",
                       (f"Préfiltre : {reason} (note {o['score']} : {o['score_reason']})", o["id"]))
        elif len(candidates) < 8:
            candidates.append(o)
    if not candidates:
        if not scored_ids:
            return [], "Aucune nouvelle annonce depuis le dernier passage."
        best = db.query(f"SELECT * FROM offers WHERE id IN ({','.join('?' * len(scored_ids))}) "
                        "ORDER BY score DESC LIMIT 10", scored_ids)
        listing = "\n".join(f"- {o['title']} — {o['company'] or '?'} ({o['location'] or '?'}) : "
                            f"{o['score']}/100, {o['score_reason']}" for o in best)
        text = llm.ask(f"{agent.MISSION}\n\nAucune annonce n'atteint {SELECT_MIN}. Voici les mieux notées de ce "
                       f"passage :\n{listing}\n\nDis en deux lignes, en français, ce qui manquait sur le marché "
                       "ces deux derniers jours. Rien d'autre.", model=llm.ECO_MODEL, step="selection")
        return [], "\n".join(text.strip().splitlines()[:2])
    listing = "\n\n".join(
        f"### ANNONCE id={o['id']} — note {o['score']} ({o['score_reason']})\nPubliée le : "
        f"{o['published'] or '?'} ; collectée le : {o['created_at'][:10]}\n{agent._offer_text(o, 2500)}"
        for o in candidates)
    res = llm.ask_json(
        f"{agent.MISSION}\n\n{agent._profile(s, 'gouts_offres', 'profil')}\n\nOFFRES DÉJÀ RETENUES OU ÉCARTÉES "
        f"PAR ANTHONY :\n{_decisions_text() or '(aucune)'}\n\nÉTAPE 4 (sélection). Nous sommes le "
        f"{datetime.date.today().isoformat()}. Parmi ces annonces notées {SELECT_MIN} ou plus, retiens-en au "
        f"plus {SELECT_MAX}, selon les critères a) à d) de ta mission. Tu peux n'en retenir aucune.\n"
        "Format : {\"retenues\": [{\"id\": entier, \"pourquoi\": \"3 lignes au plus\", "
        "\"mise_en_avant\": [\"3 puces : ce que le CV met en avant pour ce poste\"], "
        "\"ecart\": \"1 ligne : l'écart à préparer avant un entretien\"}], "
        "\"marche\": \"si aucune n'est retenue, pourquoi en deux lignes ; sinon vide\"}\n\n" + listing,
        step="selection")
    res = res if isinstance(res, dict) else {}
    by_id, picked = {o["id"]: o for o in candidates}, []
    for r in res.get("retenues") or []:
        try:
            o = by_id.get(int(r.get("id")))
        except (TypeError, ValueError, AttributeError):
            continue
        if o and o["id"] not in [p[0]["id"] for p in picked]:
            points = [str(x).strip() for x in (r.get("mise_en_avant") or []) if str(x).strip()][:3]
            picked.append((o, {"pourquoi": str(r.get("pourquoi") or "").strip(), "points": points,
                               "ecart": str(r.get("ecart") or "").strip()}))
        if len(picked) == SELECT_MAX:
            break
    return picked, "" if picked else str(res.get("marche") or "").strip()


def _hesitations(scored_ids):
    if not scored_ids:
        return ""
    rows = db.query(f"SELECT title, company, score, score_reason FROM offers WHERE id IN "
                    f"({','.join('?' * len(scored_ids))}) AND score BETWEEN 60 AND 85", scored_ids)
    return "\n".join(f"- {r['title']} — {r['company'] or '?'} : {r['score']}, {r['score_reason']}" for r in rows)


def ask_questions(s, scored_ids):
    """Un appel au modèle économique : 1 ou 2 questions fermées, 2 nouvelles par jour au plus.
    Une question déjà posée (répondue ou non) n'est jamais reposée."""
    today = _utcnow().strftime("%Y-%m-%d")
    asked_today = db.query("SELECT COUNT(*) AS n FROM questions WHERE date_pose >= ?", (today,))[0]["n"]
    open_ = db.query("SELECT texte FROM questions WHERE reponse IS NULL")
    room = min(QUESTIONS_PER_DAY - asked_today, QUESTIONS_PER_DAY - len(open_))
    if room <= 0:
        return []
    fiches = agent.angles_text([{**f, "competences": json.loads(f["competences"] or "[]")} for f in
                                db.query("SELECT * FROM cv_archive ORDER BY date DESC, id DESC LIMIT 20")])
    a_verifier = "\n".join(f"- {x}" for x in (s.get("a_verifier") or []) if str(x).strip())
    res = llm.ask_json(
        f"{agent.MISSION}\n\n{agent._profile(s)}\n\nQUESTIONS DÉJÀ RÉPONDUES (avec leur date) :\n"
        f"{agent.answers_text() or '(aucune)'}\n\nQUESTIONS EN ATTENTE DE RÉPONSE (ne les repose pas) :\n"
        f"{chr(10).join('- ' + q['texte'] for q in open_) or '(aucune)'}\n\n"
        f"CE QUI T'A FAIT HÉSITER DANS LA NOTATION :\n{_hesitations(scored_ids) or '(rien de notable)'}\n\n"
        f"INCOHÉRENCES RELEVÉES ENTRE LES CV DE L'ARCHIVE :\n{a_verifier or '(aucune)'}\n\n"
        f"FICHES D'ANGLE DE L'ARCHIVE DES CV :\n{fiches or '(vide)'}\n\n"
        f"ÉTAPE 6 (questions). Nous sommes le {today}. Pose au plus {room} question(s) fermée(s), selon les "
        "règles de ta mission (aucune si rien ne changerait tes prochaines sélections).\n"
        "Format : liste JSON d'objets {\"question\": \"…\", \"options\": [\"oui\", \"non\"]} "
        "(2 à 4 options courtes).", model=llm.ECO_MODEL, step="questions")
    known = {_norm(q["texte"]) for q in db.query("SELECT texte FROM questions")}
    created = []
    for r in res if isinstance(res, list) else []:
        if not isinstance(r, dict):
            continue
        text = str(r.get("question") or "").strip()
        options = [str(x).strip() for x in (r.get("options") or []) if str(x).strip()]
        if not text or not 2 <= len(options) <= 4 or _norm(text) in known:
            continue
        qid = db.execute("INSERT INTO questions(texte, options) VALUES(?, ?)",
                         (text, json.dumps(options, ensure_ascii=False)))
        known.add(_norm(text))
        created.append({"id": qid, "texte": text, "options": options})
        if len(created) == room:
            break
    return created


def answer(qid, value):
    rows = db.query("SELECT * FROM questions WHERE id=?", (qid,))
    if not rows:
        raise ValueError("Question introuvable.")
    q, options = rows[0], json.loads(rows[0]["options"])
    match = [o for o in options if _norm(o) == _norm(value)]
    if not match:
        raise ValueError("Réponse attendue : " + " / ".join(options) + ".")
    db.execute("UPDATE questions SET reponse=?, date_reponse=datetime('now') WHERE id=?", (match[0], qid))
    # Pour l'espace emploi de la mémoire de l'agent chef (catégories gouts_offres ou profil)
    hub.send_event("reponse_profil", f"Réponse du candidat à une question fermée de l'agent emploi, à retenir "
                   f"dans ses goûts en offres (gouts_offres) ou son profil (profil).\nQuestion : {q['texte']}\n"
                   f"Réponse : {match[0]}")
    return {"ok": True, "reponse": match[0]}


def questions():
    rows = db.query("SELECT * FROM questions ORDER BY reponse IS NOT NULL, id DESC LIMIT 30")
    for r in rows:
        r["options"] = json.loads(r["options"] or "[]")
    return rows


# ---------- consommation ----------
def consumed_since(start):
    """Jetons estimés (prompt + réponse) des appels journalisés depuis `start` (heure SQLite)."""
    r = db.query("SELECT COALESCE(SUM(taille_prompt + taille_reponse), 0) AS c FROM llm_calls WHERE created_at >= ?",
                 (start,))[0]
    return llm.estimated_tokens(r["c"])


def consumption(days=30):
    since = _sql_time(_utcnow() - datetime.timedelta(days=days))
    rows = db.query("SELECT etape, modele, COUNT(*) AS appels, SUM(taille_prompt) AS prompt, "
                    "SUM(taille_reponse) AS reponse, AVG(duree_ms) AS duree, SUM(1 - ok) AS echecs "
                    "FROM llm_calls WHERE created_at >= ? GROUP BY etape, modele ORDER BY etape, modele", (since,))
    steps = [{"etape": r["etape"], "modele": r["modele"], "appels": r["appels"],
              "taille_prompt": int(r["prompt"] or 0), "taille_reponse": int(r["reponse"] or 0),
              "jetons_estimes": llm.estimated_tokens((r["prompt"] or 0) + (r["reponse"] or 0)),
              "duree_moyenne_ms": round(r["duree"] or 0), "echecs": int(r["echecs"] or 0)} for r in rows]
    last = last_selection()
    return {"jours": days, "etapes": steps, "appels": sum(x["appels"] for x in steps),
            "jetons_estimes": sum(x["jetons_estimes"] for x in steps),
            "caracteres_par_jeton": llm.CHARS_PER_TOKEN,
            "dernier_cycle": (last or {}).get("report", {}).get("consommation")}


# ---------- la sélection ----------
def last_selection():
    rows = db.query("SELECT * FROM selections ORDER BY id DESC LIMIT 1")
    if not rows:
        return None
    r = rows[0]
    r["report"] = json.loads(r["report"] or "{}")
    r["offer_ids"] = json.loads(r["offer_ids"] or "[]")
    return r


def _offer_md(o, why, n):
    meta = " · ".join(x for x in (o["location"], o["contract"]) if x)
    link = f"[lien]({o['url']})" if o.get("url") else "pas de lien"
    points = "\n".join(f"- {p}" for p in why["points"])
    docs = (f"#### CV adapté\n\n{o['cv_md']}\n\n#### Lettre\n\n{o['letter_md']}" if o.get("cv_md")
            else "*CV et lettre non rédigés : budget du cycle atteint. Génère-les depuis la fiche de l'offre.*")
    return (f"### {n}. {o['title']} — {o['company'] or '?'}\n{meta} · {link} · publiée le {o['published'] or '?'}"
            f"\n\n**Note {o['score']}/100.** {why['pourquoi']}\n\n**Ce que le CV met en avant :**\n{points}\n\n"
            f"**L'écart à préparer :** {why['ecart']}\n\n{docs}")


def run(s, budget=None):
    """Une sélection complète. `budget` : jetons estimés au-delà desquels la rédaction est sautée."""
    if not _running.acquire(blocking=False):
        return {"status": "en_cours"}
    try:
        return _run(s, int(budget) if budget else 0)
    finally:
        _running.release()


def _run(s, budget):
    start = _sql_time(_utcnow())  # les appels de ce cycle sont ceux journalisés à partir de `start`
    collected = sources.collect_all(s)
    removed = prefilter(s)
    ids = [r["id"] for r in db.query("SELECT id FROM offers WHERE score IS NULL ORDER BY id DESC LIMIT ?",
                                     (SCORE_LIMIT,))]
    scored = score(s, ids)
    picked, market = select(s, ids)
    selected, skipped = [], 0
    for o, why in picked:
        used = consumed_since(start)
        if budget and used >= budget:
            skipped += 1  # au-delà du budget : l'offre est présentée sans CV ni lettre
        else:
            o.update(agent.generate_application(s, o))
        body = _offer_md(o, why, len(selected) + 1)
        db.execute("UPDATE offers SET selected_at=datetime('now'), selection_md=? WHERE id=?", (body, o["id"]))
        selected.append({"id": o["id"], "title": o["title"], "company": o["company"], "location": o["location"],
                         "url": o.get("url") or "", "score": o["score"], "pourquoi": why["pourquoi"],
                         "points": why["points"], "ecart": why["ecart"], "documents": bool(o.get("cv_md")),
                         "markdown": body})
    asked = ask_questions(s, ids)
    used = consumed_since(start)
    new = sum(v.get("new", 0) for v in collected.values() if isinstance(v, dict))
    lines = [f"## Sélection du {datetime.date.today().isoformat()}",
             f"{new} annonce(s) collectée(s), {removed} écartée(s) par le préfiltre, {scored} notée(s)."]
    if selected:
        lines += [x["markdown"] for x in selected]
    else:
        lines.append("**Aucune offre retenue.** " + (market or f"Aucune annonce n'atteint {SELECT_MIN}."))
    budget_note = ""
    if skipped:
        budget_note = (f"Rédaction sautée pour {skipped} offre(s) : budget du cycle atteint "
                       f"({used} jetons estimés pour {budget} prévus).")
        lines.append("**" + budget_note + "**")
    if asked:
        lines.append("## Questions\n" + "\n".join(
            f"- [Q{q['id']}] {q['texte']} ({' / '.join(q['options'])})" for q in asked))
    errors = {k: v["error"] for k, v in collected.items() if isinstance(v, dict) and v.get("error")}
    if errors:  # sans cela, une source non configurée donne « 0 annonce collectée » sans explication
        lines.insert(2, "**Sources en erreur :**\n" + "\n".join(
            f"- {SOURCE_NAMES.get(k, k)} : {e}" for k, e in errors.items()))
    report = {"collected": collected, "prefiltered": removed, "scored": scored,
              "selected": [{k: v for k, v in x.items() if k != "markdown"} for x in selected],
              "market": market, "questions": asked, "errors": errors,
              "consommation": {"jetons_estimes": used, "budget": budget, "redactions_sautees": skipped,
                               "message": budget_note}}
    md = "\n\n".join(lines)
    sid = db.execute("INSERT INTO selections(date, offer_ids, motif_si_vide, report, report_md) VALUES(?,?,?,?,?)",
                     (_sql_time(_utcnow()), json.dumps([x["id"] for x in selected]),
                      "" if selected else (market or f"Aucune annonce n'atteint {SELECT_MIN}."),
                      json.dumps(report, ensure_ascii=False), md))
    return {"status": "fait", "id": sid, "report": report, "report_md": md}
