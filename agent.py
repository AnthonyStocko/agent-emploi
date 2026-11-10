"""Le cerveau de l'agent : tri, CV + lettre, fiche de préparation, entretien."""
import json

import db
import llm

RULES = ("Règle absolue : n'invente aucune expérience, compétence, diplôme, chiffre ou date. "
         "Tu ne peux que sélectionner, réordonner et reformuler ce qui figure dans le CV maître. "
         "Si une exigence de l'annonce n'est pas couverte par le profil, ne la revendique pas.")


def _profile(s):
    cv = (s.get("cv_master") or "").strip()
    if not cv:
        raise llm.LLMError("Renseigne d'abord ton CV maître dans l'onglet Profil.")
    return (f"CV MAÎTRE DU CANDIDAT :\n{cv}\n\nCRITÈRES DE RECHERCHE :\n"
            f"Postes visés : {s.get('keywords', '')}\nLieu : {s.get('location', '')}\n"
            f"Autres critères : {s.get('criteria', '')}")


def _offer_text(o, limit=6000):
    return (f"Poste : {o['title']}\nEntreprise : {o['company']}\nLieu : {o['location']}\n"
            f"Contrat : {o['contract']}\nSalaire : {o['salary']}\n\n{(o['description'] or '')[:limit]}")


def score_pending(s, batch=8, max_batches=5):
    """Note de 0 à 100 les annonces non évaluées, les plus récentes d'abord (40 par appel
    au maximum, pour ménager le quota de l'abonnement). Renvoie (notées, restantes)."""
    profile = _profile(s)
    pending = db.query("SELECT * FROM offers WHERE score IS NULL ORDER BY id DESC "
                       "LIMIT ?", (batch * max_batches,))
    done = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        listing = "\n\n".join(f"### ANNONCE id={o['id']}\n{_offer_text(o, 1800)}" for o in chunk)
        res = llm.ask_json(
            f"{profile}\n\nÉvalue l'adéquation de chaque annonce avec ce candidat. Sois exigeant "
            "et honnête : 80+ = très bon match, 50-79 = jouable, moins de 50 = à écarter.\n"
            "Format : liste JSON d'objets {\"id\": entier, \"score\": 0-100, \"raison\": "
            "\"une phrase : points forts et principal écart\"}.\n\n" + listing)
        ids = {o["id"] for o in chunk}
        for r in res if isinstance(res, list) else []:
            try:
                oid, sc = int(r["id"]), max(0, min(100, int(r["score"])))
            except (KeyError, TypeError, ValueError):
                continue
            if oid in ids:
                db.execute("UPDATE offers SET score=?, score_reason=? WHERE id=?",
                           (sc, str(r.get("raison", "")), oid))
                done += 1
    left = db.query("SELECT COUNT(*) AS n FROM offers WHERE score IS NULL")[0]["n"]
    return done, left


def generate_application(s, o):
    text = llm.ask(
        f"{_profile(s)}\n\nANNONCE :\n{_offer_text(o)}\n\n"
        "Rédige en français, en Markdown :\n"
        "1. Un CV adapté à cette annonce (une page) : titre aligné sur le poste, accroche de "
        "2-3 lignes, expériences les plus pertinentes en premier avec des réalisations concrètes, "
        "compétences reprenant le vocabulaire de l'annonce quand le candidat les possède vraiment.\n"
        "2. Une lettre de motivation de 250 à 300 mots, spécifique à cette entreprise et à ce "
        "poste, ton naturel et direct, sans formules creuses.\n"
        f"{RULES}\n\n"
        "Format de sortie strict, sans rien d'autre :\n===CV===\n(le CV)\n===LETTRE===\n(la lettre)")
    if "===LETTRE===" not in text:
        raise llm.LLMError("Réponse du modèle mal formée, réessaie.")
    cv, letter = text.split("===LETTRE===", 1)
    cv = cv.split("===CV===", 1)[-1].strip()
    db.execute("UPDATE offers SET cv_md=?, letter_md=? WHERE id=?", (cv, letter.strip(), o["id"]))
    return {"cv_md": cv, "letter_md": letter.strip()}


def generate_prep(s, o):
    text = llm.ask(
        f"{_profile(s)}\n\nANNONCE :\n{_offer_text(o)}\n\n"
        "Prépare une fiche d'entretien en Markdown, en français, avec ces sections :\n"
        "## Ce que le recruteur cherche vraiment\n## Tes 3 meilleurs arguments\n"
        "## Tes points faibles et comment y répondre\n"
        "## 8 questions probables (avec une piste de réponse tirée du parcours réel)\n"
        "## 5 questions à poser au recruteur\n## À vérifier sur l'entreprise avant l'entretien\n"
        f"{RULES} Base-toi uniquement sur l'annonce : ne présente aucun fait sur l'entreprise "
        "que l'annonce ne mentionne pas.")
    db.execute("UPDATE offers SET prep_md=? WHERE id=?", (text, o["id"]))
    return {"prep_md": text}


def start_interview(s, o, n=6):
    qs = llm.ask_json(
        f"{_profile(s)}\n\nANNONCE :\n{_offer_text(o)}\n\n"
        f"Tu es le recruteur pour ce poste. Prépare {n} questions d'entretien à poser à l'oral, "
        "dans l'ordre : présentation, motivation pour ce poste, deux questions techniques ou "
        "métier précises liées à l'annonce, une mise en situation comportementale, puis une "
        "question difficile sur un point faible réel du profil. Questions courtes, naturelles "
        "à l'oral, vouvoiement. Format : liste JSON de chaînes.")
    qs = [str(q) for q in qs if str(q).strip()] if isinstance(qs, list) else []
    if not qs:
        raise llm.LLMError("Aucune question générée, réessaie.")
    iid = db.execute("INSERT INTO interviews(offer_id, questions) VALUES(?, ?)",
                     (o["id"], json.dumps(qs, ensure_ascii=False)))
    return {"interview_id": iid, "questions": qs}


def interview_feedback(s, interview, o, answers):
    qs = json.loads(interview["questions"])
    transcript = "\n\n".join(
        f"Question {i + 1} : {q}\nRéponse (transcription orale) : "
        f"{(answers[i] if i < len(answers) else '').strip() or '(pas de réponse)'}"
        for i, q in enumerate(qs))
    text = llm.ask(
        f"{_profile(s)}\n\nANNONCE :\n{_offer_text(o, 3000)}\n\nENTRETIEN SIMULÉ :\n{transcript}\n\n"
        "Tu es un coach d'entretien exigeant et bienveillant. Les réponses sont des "
        "transcriptions vocales : ignore les fautes de transcription. En Markdown, en français, "
        "tutoie le candidat :\n## Note globale sur 10 et impression générale\n"
        "## Retour question par question (ce qui marche, ce qui manque, une meilleure "
        "formulation fondée sur son parcours réel)\n## Tics ou longueurs repérés\n"
        "## 3 priorités pour le prochain entraînement\n"
        "Sois franc : ne complimente pas une réponse faible.")
    db.execute("UPDATE interviews SET answers=?, feedback_md=? WHERE id=?",
               (json.dumps(answers, ensure_ascii=False), text, interview["id"]))
    return {"feedback_md": text}
