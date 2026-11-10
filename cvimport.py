"""Import de CV existants : extraction du texte, puis profil défini par l'agent."""
import base64
import io
import json
import re
import zipfile

import db
import llm

MAX_FILES = 10
MAX_CHARS = 15000


class ImportError_(Exception):
    pass


def _xml_text(raw):
    raw = re.sub(r"<(?:w:tab|text:tab)[^>]*/>", "\t", raw)
    raw = re.sub(r"</(?:w:p|text:p|text:h)>|<(?:w:br|text:line-break)[^>]*/>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        raw = raw.replace(a, b)
    return raw


def extract_text(name, data):
    """Texte brut d'un CV (.pdf, .docx, .odt, .txt, .md)."""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ImportError_("Lecture des PDF indisponible : lance `pip install -r requirements.txt`.")
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:15])
        except Exception as e:
            raise ImportError_(f"{name} : PDF illisible ({e}).")
        if len(text.strip()) < 100:
            raise ImportError_(f"{name} : aucun texte trouvé (PDF scanné ou image ?). "
                               "Exporte-le en PDF texte ou en .docx.")
    elif ext in ("docx", "odt"):
        member = "word/document.xml" if ext == "docx" else "content.xml"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                text = _xml_text(z.read(member).decode("utf-8", "replace"))
        except (zipfile.BadZipFile, KeyError):
            raise ImportError_(f"{name} : fichier {ext} illisible.")
    elif ext in ("txt", "md", ""):
        text = data.decode("utf-8", "replace")
    else:
        raise ImportError_(f"{name} : format non géré (PDF, DOCX, ODT, TXT ou MD).")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()[:MAX_CHARS]


def build_profile(s, files):
    """Fusionne les CV importés en CV maître et en déduit les critères de recherche."""
    if not files:
        raise ImportError_("Choisis au moins un fichier.")
    if len(files) > MAX_FILES:
        raise ImportError_(f"{MAX_FILES} fichiers au maximum par import.")
    docs = []
    for f in files:
        try:
            data = base64.b64decode(f.get("data") or "", validate=False)
        except ValueError:
            raise ImportError_(f"{f.get('name')} : fichier illisible.")
        docs.append((str(f.get("name") or "cv"), extract_text(str(f.get("name") or ""), data)))
    existing = (s.get("cv_master") or "").strip()
    parts = [f"### CV « {name} »\n{text}" for name, text in docs]
    if existing:
        parts.insert(0, f"### CV MAÎTRE ACTUEL (à compléter, ne rien en retirer)\n{existing[:MAX_CHARS]}")
    out = llm.ask(
        "Voici plusieurs CV du même candidat, des versions adaptées à différentes offres.\n\n"
        + "\n\n".join(parts) + "\n\n"
        "TÂCHE 1 — CV maître. Fusionne-les en un seul CV exhaustif en Markdown : coordonnées, "
        "titre, toutes les expériences (de la plus récente à la plus ancienne) avec TOUTES les "
        "réalisations et chiffres distincts trouvés dans les différentes versions, compétences, "
        "formations, langues, centres d'intérêt. C'est une base de travail : sois complet, ne "
        "résume pas, ne limite pas à une page. N'invente rien et ne déduis rien : uniquement ce "
        "qui est écrit dans les CV.\n\n"
        "TÂCHE 2 — Profil de recherche, déduit des CV. JSON :\n"
        '{"postes": ["2 à 4 intitulés de poste à rechercher, courts, tels qu\'ils apparaissent '
        'dans des annonces"], "ville": "ville du candidat ou chaîne vide", "departement": '
        '"numéro du département français de cette ville ou chaîne vide", "criteres": "niveau '
        "d'expérience, secteurs et type de structure qui ressortent du parcours, en une ou deux "
        'phrases", "resume": "le profil en deux phrases, à la 2e personne (tu)", "a_verifier": '
        '["incohérences entre les versions : dates, intitulés ou chiffres qui diffèrent"]}\n'
        "Les postes doivent correspondre au niveau et au métier réels du candidat.\n\n"
        "Format de sortie strict, sans rien d'autre :\n===CV===\n(le CV maître)\n===PROFIL===\n(le JSON)")
    if "===PROFIL===" not in out:
        raise llm.LLMError("Réponse du modèle mal formée, réessaie.")
    cv, raw = out.split("===PROFIL===", 1)
    cv = cv.split("===CV===", 1)[-1].strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        p = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except ValueError:
        raise llm.LLMError("Profil illisible dans la réponse du modèle, réessaie.")
    if len(cv) < 200 or not isinstance(p, dict):
        raise llm.LLMError("CV maître vide dans la réponse du modèle, réessaie.")
    postes = [str(x).strip() for x in (p.get("postes") or []) if str(x).strip()][:4]
    values = {"cv_master": cv, "keywords": ", ".join(postes)}
    # Lieu et critères : ce que tu as déjà saisi à la main est conservé
    for key, src in (("location", "ville"), ("departement", "departement"), ("criteria", "criteres")):
        if not (s.get(key) or "").strip() and str(p.get(src) or "").strip():
            values[key] = str(p[src]).strip()
    db.set_settings(values)
    return {**values, "resume": str(p.get("resume") or ""), "files": [n for n, _ in docs],
            "a_verifier": [str(x) for x in (p.get("a_verifier") or [])][:8]}
