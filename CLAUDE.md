# Agent emploi

Lire `README.md` avant toute modification : il fixe la mission de l'agent et ses limites.

Règles non négociables :
- L'agent ne postule jamais à la place de l'utilisateur : aucun envoi de candidature, de mail ou de
  message à un recruteur, aucun formulaire soumis sur un site d'offres.
- Les prompts de rédaction gardent la règle « n'invente aucune expérience, compétence, diplôme, chiffre
  ou date » (`RULES` dans `agent.py`, et l'import des CV dans `cvimport.py`).
- Le passage tous les 2 jours (`cycle.py`) respecte la consigne `MISSION` d'`agent.py` : au plus 2 offres
  retenues (notées 80 ou plus), une seule passe de notation de 24 annonces au plus, jamais de rédaction
  pour une offre non retenue, au plus 2 questions fermées par jour.
- La boîte mail est en lecture seule (`readonly=True`) et limitée aux expéditeurs d'alertes listés
  (`IMAP_SENDERS`) : aucun envoi, aucun déplacement, aucune suppression, aucun mail marqué comme lu.
- Claude Code reste sans outil (`--tools ""`, `--strict-mcp-config`), dans un dossier vide, avec le seul
  jeton d'abonnement dans son environnement.
- Les routes `/api/` exigent une session ou le jeton `HUB_TOKEN` ; sur Render, sans `APP_PASSWORD`,
  l'appli refuse toute demande (réponse 503).
- Ne jamais écrire de jeton, de clé ni de mot de passe dans un fichier du dépôt ; `data.sqlite3` n'est
  jamais commité.
- L'agent reste dans son domaine (recherche d'emploi) : pas de traitement général des mails, pas
  d'agenda, pas de modification de code.
- Avant de terminer : `python -m unittest discover -p 'test_*.py'`.
