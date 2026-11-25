# Image Python + Node.js : Node.js est nécessaire pour Claude Code
FROM python:3.12-slim

COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

# Utilisateur sans droits administrateur (son dossier personnel accueille ~/.claude).
# Le dossier de l'application lui appartient pour qu'il puisse y écrire data.sqlite3.
RUN useradd --create-home app && mkdir -p /home/app/src && chown app:app /home/app/src
WORKDIR /home/app/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
USER app

# HEBERGE=1 : écoute sur 0.0.0.0, APP_PASSWORD et CLAUDE_CODE_OAUTH_TOKEN obligatoires, cookie Secure,
# chez n'importe quel hébergeur (Render, Coolify sur Oracle Cloud, serveur à la maison).
# Le port est lu dans PORT (8765 par défaut). Base persistante : monter un volume et définir DATA_DIR.
ENV PYTHONUNBUFFERED=1 \
    HEBERGE=1
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.getenv('PORT','8765'), timeout=4)"

CMD ["python", "app.py"]
