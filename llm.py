"""Appels au modèle via Claude Code en mode non interactif (`claude -p`).

Utilise la session Claude Code déjà connectée sur ta machine, donc ton abonnement.
La variable ANTHROPIC_API_KEY est retirée de l'environnement du sous-processus pour
éviter une facturation à l'usage par l'API.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile


class LLMError(Exception):
    pass


def ask(prompt, timeout=300):
    exe = shutil.which("claude")
    if not exe:
        raise LLMError("Commande `claude` introuvable. Installe Claude Code et connecte-toi "
                       "(lance `claude` une fois dans un terminal), puis relance l'appli.")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    workdir = tempfile.mkdtemp(prefix="agent-emploi-")  # dossier vide : pas de contexte projet
    try:
        proc = subprocess.run(
            [exe, "-p", "Suis exactement les instructions reçues sur l'entrée standard. "
                        "Réponds directement, sans utiliser d'outil.",
             "--output-format", "json"],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, cwd=workdir, env=env)
    except subprocess.TimeoutExpired:
        raise LLMError("Claude Code n'a pas répondu à temps. Réessaie.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        raise LLMError("Erreur Claude Code : " + (proc.stderr or "").strip()[:500])
    try:
        data = json.loads(out)
    except ValueError:
        return out
    if isinstance(data, dict):
        if data.get("is_error"):
            raise LLMError("Erreur Claude Code : " + str(data.get("result"))[:500])
        return str(data.get("result", "")).strip()
    return out


def ask_json(prompt, timeout=300):
    """Demande une réponse JSON et l'extrait même si elle est entourée de texte."""
    text = ask(prompt + "\n\nRéponds uniquement avec du JSON valide, sans texte autour.", timeout)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    raise LLMError("Réponse du modèle illisible (JSON attendu).")
