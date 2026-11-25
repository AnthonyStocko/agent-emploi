"""Appels au modèle via Claude Code en mode non interactif (`claude -p`).

En local : la session Claude Code déjà connectée sur ta machine, donc ton abonnement.
Sur un serveur (Render) : le jeton d'abonnement créé sur ton PC avec `claude setup-token`,
placé dans la variable d'environnement CLAUDE_CODE_OAUTH_TOKEN.

Claude Code est lancé sans aucun outil (ni fichiers, ni commandes, ni web), dans un dossier
vide, et ne reçoit que les variables d'environnement de la liste ci-dessous : ni APP_PASSWORD
ni ANTHROPIC_API_KEY (sinon il utiliserait l'API payante) ne lui sont visibles.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import db


CLI_TIMEOUT_SECONDS = int(os.getenv("CLI_TIMEOUT_SECONDS", "300"))
CLI_ENV_ALLOWLIST = [
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "COMSPEC", "PATHEXT",  # Windows
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    "CLAUDE_CODE_OAUTH_TOKEN",
]


class LLMError(Exception):
    pass


# Modèle économique (notation, questions fermées) ; le modèle principal (CLAUDE_CLI_MODEL, sinon
# celui de Claude Code) rédige les CV et les lettres.
ECO_MODEL = os.getenv("CLAUDE_CLI_MODEL_ECO", "haiku")
CHARS_PER_TOKEN = 4  # estimation des jetons à partir de la taille des textes


def heberge():
    """Vrai quand l'appli tourne chez un hébergeur (Render fournit RENDER ; ailleurs, Coolify sur Oracle Cloud
    ou un serveur à la maison, le Dockerfile fournit HEBERGE=1) : mot de passe et jeton deviennent obligatoires."""
    return bool(os.getenv("RENDER") or os.getenv("HEBERGE"))


def _command(model=None):
    exe = shutil.which("claude")
    if not exe:
        raise LLMError("Claude Code est introuvable. En local : installe-le et lance `claude` une "
                       "fois pour te connecter. Sur Render : déploie avec le Dockerfile.")
    token = (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if heberge() and not token:
        raise LLMError("Renseigne CLAUDE_CODE_OAUTH_TOKEN (obtenu avec `claude setup-token`) "
                       "dans les variables d'environnement de l'hébergeur.")
    if token.startswith("sk-ant-api"):
        raise LLMError("CLAUDE_CODE_OAUTH_TOKEN contient une clé API (sk-ant-api…), facturée au "
                       "crédit. Mets le jeton d'abonnement (sk-ant-oat01-…) de `claude setup-token`.")
    command = [exe, "-p", "--tools", "", "--strict-mcp-config", "--output-format", "json"]
    model = model or os.getenv("CLAUDE_CLI_MODEL")
    if model:
        command += ["--model", model]
    return command


def ask(prompt, timeout=None, model=None, step="autre"):
    """Appel au modèle. Chaque appel est journalisé (table llm_calls) : étape, modèle, tailles et durée,
    jamais le contenu du prompt ni de la réponse."""
    start, text = time.monotonic(), ""
    try:
        text = _run(prompt, timeout, model)
        return text
    finally:
        _record(step, model or os.getenv("CLAUDE_CLI_MODEL") or "défaut", len(prompt), len(text),
                int((time.monotonic() - start) * 1000), bool(text))


def _record(step, model, prompt_chars, answer_chars, ms, ok):
    try:
        db.execute("INSERT INTO llm_calls(etape, modele, taille_prompt, taille_reponse, duree_ms, ok) "
                   "VALUES(?,?,?,?,?,?)", (str(step)[:40], str(model)[:60], prompt_chars, answer_chars, ms, int(ok)))
    except Exception as e:  # le journal ne doit jamais faire échouer un appel
        print(f"Journal des appels indisponible : {e!r}", flush=True)


def estimated_tokens(chars):
    return int(chars) // CHARS_PER_TOKEN


def _run(prompt, timeout=None, model=None):
    command = _command(model)
    env = {k: os.environ[k] for k in CLI_ENV_ALLOWLIST if k in os.environ}
    workdir = tempfile.mkdtemp(prefix="agent-emploi-")  # dossier vide : rien à lire
    try:
        proc = subprocess.run(command, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout or CLI_TIMEOUT_SECONDS, cwd=workdir, env=env)
    except subprocess.TimeoutExpired:
        raise LLMError("Claude Code n'a pas répondu à temps. Réessaie.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out)
    except ValueError:
        data = {"is_error": True, "result": out or (proc.stderr or "").strip()}
    if not isinstance(data, dict):
        data = {"is_error": True, "result": out}
    message = " ".join(str(data.get("result") or "").split())
    if data.get("is_error") or proc.returncode != 0:
        print(f"Erreur Claude Code (code {proc.returncode}) : {message[-500:]!r}", flush=True)
        if "limit" in message.lower():
            raise LLMError("Limite d'usage de l'abonnement atteinte : " + message[:200])
        raise LLMError("Erreur Claude Code : " + (message[:300] or str(proc.returncode)))
    return str(data.get("result") or "").strip()


def ask_json(prompt, timeout=None, model=None, step="autre"):
    """Demande une réponse JSON et l'extrait même si elle est entourée de texte."""
    text = ask(prompt + "\n\nRéponds uniquement avec du JSON valide, sans texte autour.", timeout, model, step)
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
