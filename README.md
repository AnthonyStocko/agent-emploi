# Agent emploi

Appli web locale qui cherche des annonces, les note selon ton profil, rédige un CV et une
lettre adaptés, suit tes candidatures et te fait passer des entretiens simulés à la voix.

## Prérequis

- Python 3.9 ou plus récent. Une seule bibliothèque, pour lire les CV en PDF :
  `pip install -r requirements.txt` (le reste fonctionne sans).
- Claude Code installé et connecté avec ton abonnement : lance `claude` une fois dans un
  terminal pour vérifier. L'appli appelle `claude -p` en arrière-plan.
- Chrome ou Edge pour la simulation vocale (Firefox ne gère pas la reconnaissance vocale ;
  la saisie au clavier reste possible).

## Lancement

    python app.py

Puis ouvre http://127.0.0.1:8765 (autre port : `python app.py 9000`).

## Mise en route

1. **Profil** : importe tes CV existants (PDF, DOCX, ODT, TXT). L'agent les fusionne en CV
   maître, en déduit les postes à viser et le lieu, puis cherche et note les offres. Tu peux
   aussi coller ton CV maître et saisir tes critères à la main. Relis le résultat : tout est
   modifiable.
2. **Réglages** : ajoute les identifiants des sources que tu veux utiliser.
   - France Travail : compte sur francetravail.io, application abonnée à l'API
     « Offres d'emploi v2 ».
   - Adzuna : clé gratuite sur developer.adzuna.com.
   - Alertes mail : accès IMAP en lecture seule. Pour Gmail, crée un mot de passe
     d'application (validation en deux étapes requise).
3. **Annonces** : « Chercher des annonces », puis « Noter les nouvelles ».
   Tu peux aussi coller une annonce ou son lien.
4. Ouvre une annonce : génère CV + lettre, la fiche d'entretien, puis lance la simulation.

## Fonctionnement de la simulation

Le recruteur pose 6 questions à voix haute, ta réponse est transcrite en direct (tu peux la
corriger), puis le coach rend un retour détaillé, conservé dans la fiche de l'annonce.

## Déploiement sur Render (avec ton abonnement Claude)

Même principe que agent-perso : Claude Code est installé dans l'image Docker et se connecte
avec un jeton d'abonnement.

1. Sur ton PC : `claude setup-token`, puis copie le jeton (`sk-ant-oat01-…`).
2. Sur Render : New > Blueprint, choisis ce dépôt (le fichier `render.yaml` crée le service).
3. Renseigne les variables demandées :
   - `APP_PASSWORD` : le mot de passe de l'appli (obligatoire, l'appli refuse de démarrer sans).
   - `CLAUDE_CODE_OAUTH_TOKEN` : le jeton de l'étape 1.
   - `APP_USERNAME` : identifiant de connexion (`admin` par défaut).
4. Facultatif, pour ne pas ressaisir les sources après chaque déploiement : `FT_CLIENT_ID`,
   `FT_CLIENT_SECRET`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `IMAP_HOST`, `IMAP_USER`,
   `IMAP_PASSWORD`, `IMAP_SENDERS`, `IMAP_DAYS`. Autres réglages : `CLAUDE_CLI_MODEL`,
   `CLI_TIMEOUT_SECONDS`, `APP_SESSION_DAYS`, `APP_SESSION_SECRET`.

**Données** : sans disque persistant, la base (CV maître, annonces, suivi) est effacée à chaque
redéploiement ou redémarrage. Deux solutions : un disque Render monté sur `/data` avec
`DATA_DIR=/data` (voir `render.yaml`), ou le bouton « Télécharger une sauvegarde » de l'onglet
Réglages, puis « Restaurer une sauvegarde ».

Claude Code tourne sans aucun outil, dans un dossier vide, et ne voit que le jeton
d'abonnement : ni le mot de passe de l'appli ni tes identifiants mail.

## Lien avec l'agent chef (facultatif)

Avec les variables `HUB_URL` (adresse de l'agent chef) et `HUB_TOKEN` (secret partagé), l'agent
emploi relit la mémoire commune avant de noter, rédiger ou préparer un entretien, et signale à
l'agent chef tes décisions (offre écartée, retenue, postulée), tes corrections de textes et tes
entretiens simulés. Sans ces variables, ou si l'agent chef est injoignable, rien ne change.

## À savoir

- Chaque appel à Claude prend de 10 secondes à 2 minutes et consomme ton quota d'abonnement.
  La notation traite 8 annonces par appel, 40 par clic au maximum.
- Tout est stocké dans `data.sqlite3`, à côté du code, y compris tes identifiants en clair.
  Ne partage pas ce fichier et ne le commite pas (il est dans `.gitignore`).
- En local, le serveur n'écoute que sur 127.0.0.1. Sur Render, tout passe par la page de connexion.
- L'agent a pour consigne de ne rien inventer, mais relis toujours CV et lettre avant envoi.
- Statut « Postulée » : l'étiquette « À relancer » apparaît au bout de 7 jours.

## Fichiers

- `app.py` : serveur et routes — `agent.py` : prompts (tri, rédaction, coaching)
- `cvimport.py` : import des CV et profil automatique — `sources.py` : collecte — `llm.py` : appel à Claude Code — `db.py` : base SQLite
- `static/` : interface
