"""Import de CV existants : extraction du texte, puis profil défini par l'agent."""
import base64
import io
import json
import re
import zipfile

import db
import llm

MAX_FILES = 10           # fichiers envoyés un par un
MAX_ZIP_FILES = 40       # CV dans une archive .zip
MAX_ZIP_BYTES = 60_000_000      # taille décompressée totale d'une archive
MAX_MEMBER_BYTES = 10_000_000   # taille décompressée d'un fichier de l'archive
MAX_CHARS = 15000
TOTAL_CHARS = 150000     # texte de tous les CV dans le prompt : chaque CV est raccourci au besoin
CV_EXTENSIONS = ("pdf", "docx", "odt", "txt", "md")


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


def unzip(name, data):
    """CV contenus dans une archive .zip : [(nom, octets, date AAAA-MM-JJ du fichier)]."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_(f"{name} : archive .zip illisible.")
    members = [m for m in z.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX/")
               and not m.filename.rsplit("/", 1)[-1].startswith(".")
               and m.filename.lower().rsplit(".", 1)[-1] in CV_EXTENSIONS]
    if not members:
        raise ImportError_(f"{name} : aucun CV (PDF, DOCX, ODT, TXT ou MD) dans l'archive.")
    if len(members) > MAX_ZIP_FILES:
        raise ImportError_(f"{name} : {len(members)} CV dans l'archive, {MAX_ZIP_FILES} au maximum.")
    if sum(m.file_size for m in members) > MAX_ZIP_BYTES or any(m.file_size > MAX_MEMBER_BYTES for m in members):
        raise ImportError_(f"{name} : archive trop lourde une fois décompressée.")
    out = []
    for m in members:
        y, mo, d = m.date_time[:3]
        out.append((m.filename.rsplit("/", 1)[-1], z.read(m), f"{y:04d}-{mo:02d}-{d:02d}"))
    return out


def _decode_files(files):
    """[(nom, octets, date ou "")] : fichiers envoyés et contenu des archives .zip."""
    if not files:
        raise ImportError_("Choisis au moins un fichier.")
    if len(files) > MAX_FILES:
        raise ImportError_(f"{MAX_FILES} fichiers au maximum par import (ou une archive .zip de "
                           f"{MAX_ZIP_FILES} CV).")
    out = []
    for f in files:
        name = str(f.get("name") or "cv")
        try:
            data = base64.b64decode(f.get("data") or "", validate=False)
        except ValueError:
            raise ImportError_(f"{name} : fichier illisible.")
        out += unzip(name, data) if name.lower().endswith(".zip") else [(name, data, "")]
    if len(out) > MAX_ZIP_FILES:
        raise ImportError_(f"{MAX_ZIP_FILES} CV au maximum par import.")
    return out


def save_angles(fiches, names, dates):
    """Une fiche d'angle par fichier (table cv_archive) ; un fichier réimporté remplace sa fiche."""
    saved = 0
    for f in fiches if isinstance(fiches, list) else []:
        if not isinstance(f, dict) or str(f.get("fichier") or "") not in names:
            continue
        name = str(f["fichier"])
        comps = [str(c).strip() for c in (f.get("competences") or []) if str(c).strip()][:12]
        db.execute("DELETE FROM cv_archive WHERE fichier=?", (name,))
        db.execute("INSERT INTO cv_archive(fichier, poste_vise, titre, competences, date) VALUES(?,?,?,?,?)",
                   (name, str(f.get("poste_vise") or "").strip(), str(f.get("titre") or "").strip(),
                    json.dumps(comps, ensure_ascii=False), str(f.get("date") or "").strip() or dates.get(name, "")))
        saved += 1
    return saved


def build_profile(s, files):
    """Fusionne les CV importés en CV maître, en déduit les critères de recherche et garde une fiche
    d'angle par fichier (poste visé, titre, compétences mises en avant, date)."""
    raw_files = _decode_files(files)
    per_file = min(MAX_CHARS, max(2500, TOTAL_CHARS // len(raw_files)))
    docs = [(name, extract_text(name, data)[:per_file]) for name, data, _ in raw_files]
    dates = {name: date for name, _, date in raw_files if date}
    existing = (s.get("cv_master") or "").strip()
    parts = [f"### CV « {name} »" + (f" (fichier daté du {dates[name]})" if name in dates else "") + f"\n{text}"
             for name, text in docs]
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
        '["incohérences entre les versions : dates, intitulés ou chiffres qui diffèrent"], '
        '"fiches": [une fiche d\'angle par fichier : {"fichier": "nom exact du fichier", "poste_vise": '
        '"le poste auquel ce CV répondait, d\'après son contenu", "titre": "le titre utilisé en tête du CV", '
        '"competences": ["3 à 8 compétences mises en avant dans cette version"], "date": "AAAA-MM-JJ ou '
        'AAAA-MM si le CV la donne, sinon chaîne vide"}]}\n'
        "Les postes doivent correspondre au niveau et au métier réels du candidat.\n\n"
        "Format de sortie strict, sans rien d'autre :\n===CV===\n(le CV maître)\n===PROFIL===\n(le JSON)",
        step="import_cv")
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
    a_verifier = [str(x).strip() for x in (p.get("a_verifier") or []) if str(x).strip()][:8]
    # Les incohérences entre versions nourrissent les questions fermées du passage suivant
    values["a_verifier"] = a_verifier
    db.set_settings(values)
    angles = save_angles(p.get("fiches"), {n for n, _ in docs}, dates)
    return {**values, "resume": str(p.get("resume") or ""), "files": [n for n, _ in docs],
            "a_verifier": a_verifier, "fiches": angles}
