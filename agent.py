"""Le cerveau de l'agent : tri, CV + lettre, fiche de préparation, entretien."""
import json

import db
import hub
import llm

MISSION = """RÔLE
Tu es l'agent emploi d'Anthony. Ta mission : tous les 2 jours, lui présenter 1 ou 2 offres
très ciblées, chacune accompagnée d'un CV adapté et d'une lettre de motivation prêts à relire.
Tu travailles seul, sans qu'on te le demande. Mieux vaut aucune offre qu'une offre moyenne.

SOURCES DE VÉRITÉ (par ordre d'autorité)
1. Le CV maître : la seule source de faits (expériences, compétences, chiffres, dates, diplômes).
2. Les réponses d'Anthony à tes questions fermées et la mémoire de l'agent chef (goûts en offres,
   récits d'expérience, style d'écriture, points à travailler) : elles orientent tes choix,
   jamais le contenu factuel.
3. L'archive des CV déjà envoyés : elle montre quels angles Anthony a déjà pris selon les postes
   (intitulé mis en avant, compétences remontées, formulations). Sers-t'en pour les reprendre
   quand ils collent au poste ; elle ne t'autorise pas à revendiquer un fait absent du CV maître.

CYCLE (un passage tous les 2 jours)
1. Collecte : nouvelles annonces des sources configurées, sans doublons ni offres déjà vues,
   écartées ou postulées.
2. Préfiltre sans modèle : élimine les annonces hors zone, hors type de contrat, ou contenant
   un mot exclu par Anthony.
3. Notation (de 0 à 100) des annonces restantes, avec une phrase par annonce : point fort
   principal et écart principal.
4. Sélection : parmi les annonces notées 80 ou plus, retiens-en au plus 2.
   Critères, dans l'ordre :
   a) adéquation réelle entre les exigences clés de l'annonce et le CV maître ;
   b) cohérence avec les goûts connus (offres retenues ou écartées par le passé, réponses aux questions) ;
   c) fraîcheur de l'annonce (moins de 7 jours de préférence) ;
   d) diversité : ne retiens pas deux postes quasi identiques.
   Si aucune annonce n'atteint 80, ne retiens rien et dis pourquoi en deux lignes
   (ce qui manquait sur le marché ces deux derniers jours).
5. Rédaction, pour chaque offre retenue :
   - CV d'une page : titre aligné sur l'intitulé de l'annonce, accroche de 2 à 3 lignes qui répond
     au besoin principal du poste, expériences réordonnées par pertinence, réalisations chiffrées
     en premier, vocabulaire de l'annonce repris uniquement pour ce qu'Anthony possède vraiment.
     Retire ce qui n'apporte rien à ce poste.
   - Lettre de 250 à 300 mots : pourquoi ce poste et cette entreprise (seulement d'après l'annonce),
     2 preuves tirées du parcours qui répondent aux 2 exigences principales, une conclusion
     sans formule creuse. Style : celui d'Anthony, d'après la mémoire.
6. Questions : pose 1 ou 2 questions fermées (voir plus bas).

QUESTIONS FERMÉES
- Au plus 2 par jour, chacune avec une réponse par oui ou non, ou un choix parmi 2 à 4 options.
- Ne pose une question que si sa réponse changera tes prochaines sélections. Choisis en priorité :
  ce qui t'a fait hésiter dans la notation, les incohérences relevées entre les CV de l'archive,
  les critères absents (salaire minimum, télétravail, mobilité, taille d'entreprise, secteurs à éviter).
- Ne repose jamais une question déjà répondue, sauf si une réponse a plus de 3 mois
  et que les sélections récentes la contredisent.
- Exemples de forme : « Un poste à plus de 45 minutes de chez toi, c'est exclu ? (oui / non) »,
  « Tu vises plutôt : PME / grand groupe / indifférent ? »

BUDGET
Chaque appel au modèle consomme le quota d'Anthony. Par cycle : une seule passe de notation
(24 annonces au plus, après le préfiltre), un appel de sélection, au plus 2 rédactions
et un appel pour les questions. Ne rédige jamais pour une offre non retenue.
Utilise le modèle économique pour la notation et les questions, le modèle principal
pour la rédaction.

INTERDITS (non négociables)
- N'invente aucune expérience, compétence, diplôme, chiffre ou date. Tu peux seulement sélectionner,
  réordonner et reformuler ce qui figure dans le CV maître. Une exigence non couverte n'est jamais revendiquée.
- Ne présente aucun fait sur l'entreprise que l'annonce ne mentionne pas.
- Ne postule jamais et ne contacte aucun recruteur : Anthony relit et envoie lui-même.

FORMAT DE LA SÉLECTION (pour chaque offre)
- Intitulé, entreprise, lieu, contrat, lien, date de publication
- Note et pourquoi cette offre (3 lignes au plus)
- Ce que le CV met en avant pour ce poste (3 puces)
- L'écart à préparer avant un entretien (1 ligne)
- Le CV adapté, puis la lettre"""

RULES = ("Règle absolue : n'invente aucune expérience, compétence, diplôme, chiffre ou date. "
         "Tu ne peux que sélectionner, réordonner et reformuler ce qui figure dans le CV maître. "
         "Si une exigence de l'annonce n'est pas couverte par le profil, ne la revendique pas.")


def _profile(s, *memory_categories):
    cv = (s.get("cv_master") or "").strip()
    if not cv:
        raise llm.LLMError("Renseigne d'abord ton CV maître dans l'onglet Profil.")
    text = (f"CV MAÎTRE DU CANDIDAT :\n{cv}\n\nCRITÈRES DE RECHERCHE :\n"
            f"Postes visés : {s.get('keywords', '')}\nLieu : {s.get('location', '')}\n"
            f"Autres critères : {s.get('criteria', '')}")
    # Mémoire commune tenue par l'agent chef : ce que le candidat a montré ou dit au fil du temps
    remembered = hub.memory_text(*memory_categories) if memory_categories else ""
    if remembered:
        text += ("\n\nCE QUE L'ON A APPRIS DU CANDIDAT AU FIL DU TEMPS (à prendre en compte ; "
                 f"le CV maître reste la seule source de faits pour le CV et la lettre) :\n{remembered}")
    return text


def _offer_text(o, limit=6000):
    return (f"Poste : {o['title']}\nEntreprise : {o['company']}\nLieu : {o['location']}\n"
            f"Contrat : {o['contract']}\nSalaire : {o['salary']}\n\n{(o['description'] or '')[:limit]}")


def score_pending(s, batch=8, max_batches=5):
    """Note de 0 à 100 les annonces non évaluées, les plus récentes d'abord (40 par appel
    au maximum, pour ménager le quota de l'abonnement). Renvoie (notées, restantes)."""
    profile = _profile(s, "gouts_offres", "profil")
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


def answers_text():
    """Réponses d'Anthony aux questions fermées, avec leur date."""
    rows = db.query("SELECT text, answer, answered_at FROM questions WHERE answer IS NOT NULL ORDER BY id")
    return "\n".join(f"- {r['text']} → {r['answer']} (répondu le {(r['answered_at'] or '')[:10]})" for r in rows)


def archive_text(limit=900, n=6):
    """CV déjà envoyés (offres postulées) : les angles déjà pris selon les postes."""
    rows = db.query("SELECT title, company, cv_md FROM offers WHERE cv_md IS NOT NULL AND status IN "
                    "('postulee', 'entretien', 'refus', 'offre') ORDER BY id DESC LIMIT ?", (n,))
    return "\n\n".join(f"--- CV envoyé pour « {r['title']} » ({r['company'] or '?'}) :\n{r['cv_md'][:limit]}"
                       for r in rows)


def generate_application(s, o):
    """CV et lettre pour une offre, au modèle principal (étape 5 de la mission)."""
    answers, archive = answers_text(), archive_text()
    text = llm.ask(
        f"TA MISSION (contexte) :\n{MISSION}\n\n{_profile(s, 'style_ecriture', 'recits', 'profil')}\n\n"
        + (f"RÉPONSES D'ANTHONY À TES QUESTIONS FERMÉES :\n{answers}\n\n" if answers else "")
        + (f"ARCHIVE DES CV DÉJÀ ENVOYÉS (angles déjà pris, pas une source de faits) :\n{archive}\n\n"
           if archive else "")
        + f"ANNONCE :\n{_offer_text(o)}\n\n"
        "ÉTAPE 5 (rédaction). Rédige en français, en Markdown :\n"
        "1. Un CV d'une page : titre aligné sur l'intitulé de l'annonce, accroche de 2 à 3 lignes qui "
        "répond au besoin principal du poste, expériences réordonnées par pertinence, réalisations "
        "chiffrées en premier, vocabulaire de l'annonce repris uniquement pour ce que le candidat possède "
        "vraiment. Retire ce qui n'apporte rien à ce poste.\n"
        "2. Une lettre de 250 à 300 mots : pourquoi ce poste et cette entreprise (seulement d'après "
        "l'annonce), 2 preuves tirées du parcours qui répondent aux 2 exigences principales, une "
        "conclusion sans formule creuse, dans le style du candidat d'après la mémoire.\n"
        f"{RULES} Ne présente aucun fait sur l'entreprise que l'annonce ne mentionne pas.\n\n"
        "Format de sortie strict, sans rien d'autre :\n===CV===\n(le CV)\n===LETTRE===\n(la lettre)")
    if "===LETTRE===" not in text:
        raise llm.LLMError("Réponse du modèle mal formée, réessaie.")
    cv, letter = text.split("===LETTRE===", 1)
    cv = cv.split("===CV===", 1)[-1].strip()
    db.execute("UPDATE offers SET cv_md=?, letter_md=? WHERE id=?", (cv, letter.strip(), o["id"]))
    return {"cv_md": cv, "letter_md": letter.strip()}


def generate_prep(s, o):
    text = llm.ask(
        f"{_profile(s, 'points_a_travailler', 'recits')}\n\nANNONCE :\n{_offer_text(o)}\n\n"
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
        f"{_profile(s, 'points_a_travailler')}\n\nANNONCE :\n{_offer_text(o)}\n\n"
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
        f"{_profile(s, 'points_a_travailler')}\n\nANNONCE :\n{_offer_text(o, 3000)}\n\nENTRETIEN SIMULÉ :\n{transcript}\n\n"
        "Tu es un coach d'entretien exigeant et bienveillant. Les réponses sont des "
        "transcriptions vocales : ignore les fautes de transcription. En Markdown, en français, "
        "tutoie le candidat :\n## Note globale sur 10 et impression générale\n"
        "## Retour question par question (ce qui marche, ce qui manque, une meilleure "
        "formulation fondée sur son parcours réel)\n## Tics ou longueurs repérés\n"
        "## 3 priorités pour le prochain entraînement\n"
        "Sois franc : ne complimente pas une réponse faible.")
    db.execute("UPDATE interviews SET answers=?, feedback_md=? WHERE id=?",
               (json.dumps(answers, ensure_ascii=False), text, interview["id"]))
    hub.send_event("entretien", f"Entretien simulé pour le poste « {o['title']} » chez "
                   f"{o['company'] or '?'}.\n\n{transcript[:4500]}\n\nRETOUR DU COACH :\n{text[:2500]}")
    return {"feedback_md": text}
