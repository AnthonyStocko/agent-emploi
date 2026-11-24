# Agent emploi

## Mission

T'aider dans ta recherche d'emploi : chercher des annonces, les noter selon ton profil, rédiger un CV
et une lettre adaptés à chacune, suivre tes candidatures et te faire passer des entretiens simulés à la voix.

Tous les 2 jours, il travaille seul : il te présente 1 ou 2 offres très ciblées, chacune avec un CV et
une lettre prêts à relire, et te pose 1 ou 2 questions fermées pour mieux choisir la fois suivante.
Mieux vaut aucune offre qu'une offre moyenne. Sa consigne complète est `MISSION` dans `agent.py`.

## Ce qu'il fait

| Domaine | Ce qu'il fait |
|---|---|
| Profil | Importe tes CV (PDF, DOCX, ODT, TXT), les fusionne en CV maître, en déduit les postes à viser et le lieu |
| Annonces | Collecte sur France Travail, Adzuna, dans tes alertes mail et à partir d'une annonce collée ; note chaque annonce selon ton profil |
| Sélection | Tous les 2 jours : collecte, préfiltre sans modèle (zone, contrat, mots exclus, doublons), une passe de notation (24 annonces au plus), puis au plus 2 offres notées 80 ou plus, avec CV et lettre ; sinon, deux lignes sur ce qui manquait |
| Questions | 1 ou 2 questions fermées par passage (2 par jour au plus), à réponse oui / non ou à choix ; tes réponses orientent les sélections suivantes |
| Candidature | Rédige un CV et une lettre adaptés à l'annonce, que tu relis et envoies toi-même |
| Suivi | Garde le statut de chaque annonce (écartée, retenue, postulée) et signale celles à relancer |
| Entretien | Prépare une fiche d'entretien, pose les questions à voix haute, puis rend un retour détaillé |

Tout ce qu'il écrit reste dans sa propre base (`data.sqlite3`).

## Ce qu'il ne fait jamais

- **Postuler à ta place.** Il n'envoie aucune candidature et ne contacte aucun recruteur : le statut
  « Postulée » est une étiquette que tu poses toi-même.
- **Inventer.** Aucune expérience, compétence, diplôme, chiffre ou date qui ne soit pas dans ton CV
  maître. Relis quand même toujours CV et lettre avant envoi.
- **Écrire dans ta boîte mail.** Il la lit en lecture seule, et seulement les mails des expéditeurs
  d'alertes que tu as listés (`IMAP_SENDERS`). Le reste de la boîte est le domaine d'agent-perso, qui
  laisse ces alertes de côté (sa variable `JOB_ALERT_SENDERS` doit contenir la même liste).
- **Donner des outils ou des secrets à Claude.** Claude Code tourne sans aucun outil, dans un dossier
  vide, et ne voit que le jeton d'abonnement : ni le mot de passe de l'appli ni tes identifiants mail.
- **Sortir de son domaine.** Les mails, l'agenda et les tâches sont le rôle d'agent-perso, la
  modification du code celui d'agent-dev.

Les règles qui s'imposent à toute modification du code sont dans [CLAUDE.md](CLAUDE.md).

## Qui l'appelle

- **Toi**, depuis sa page web (connexion par `APP_USERNAME` / `APP_PASSWORD`).
- **L'agent chef** ([agent-manager](https://github.com/AnthonyStocko/agent-manager)), qui appelle ses
  routes `/api/` avec `HUB_TOKEN` : dans la discussion, et chaque matin en semaine pour chercher et
  noter les offres. En retour, l'agent emploi lit la mémoire commune de l'agent chef et lui signale tes
  décisions (voir « Lien avec l'agent chef »).

L'agent développeur ([agent-dev](https://github.com/AnthonyStocko/agent-dev)) modifie ce dépôt et
fusionne dans `main` sans relecture si toutes les vérifications passent.

Il tourne sur Render (voir « Déploiement sur Render ») et peut aussi être lancé en local.

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

## Le passage tous les 2 jours

Le point du matin de l'agent chef appelle `POST /api/cycle` chaque jour ouvré ; l'agent ne fait un
passage que si le précédent date d'au moins 44 heures (`{"force": true}` passe outre, comme le bouton
« Lancer un passage maintenant »). Un passage coûte au plus 5 appels au modèle : une notation et les
questions au modèle économique (`CLAUDE_CLI_MODEL_ECO`, `haiku` par défaut), la sélection et au plus
2 rédactions au modèle principal (`CLAUDE_CLI_MODEL`, sinon celui de Claude Code). Il ne rédige jamais
pour une offre non retenue. Les annonces écartées par le préfiltre passent au statut « Écartée » avec
la raison « Préfiltre : … ».

Le préfiltre se règle dans l'onglet Profil : zone acceptée (communes, numéros de département),
types de contrat acceptés et mots qui excluent une annonce. Un réglage vide ne filtre rien.

Routes : `GET /api/cycle` (dernier passage et questions), `POST /api/cycle`,
`POST /api/questions/<id>/answer` avec `{"answer": "<une des options>"}`. Chaque réponse est aussi
signalée à l'agent chef pour la mémoire commune.

## Fonctionnement de la simulation

Le recruteur pose 6 questions à voix haute, ta réponse est transcrite en direct (tu peux la
corriger), puis le coach rend un retour détaillé, conservé dans la fiche de l'annonce.

## Déploiement sur Render (avec ton abonnement Claude)

Même principe que agent-perso : Claude Code est installé dans l'image Docker et se connecte
avec un jeton d'abonnement.

1. Sur ton PC : `claude setup-token`, puis copie le jeton (`sk-ant-oat01-…`).
2. Sur Render : New > Blueprint, choisis ce dépôt (le fichier `render.yaml` crée le service).
3. Renseigne les variables demandées :
   - `APP_PASSWORD` : le mot de passe de l'appli (obligatoire : sans lui, l'appli refuse toute demande).
   - `CLAUDE_CODE_OAUTH_TOKEN` : le jeton de l'étape 1.
   - `APP_USERNAME` : identifiant de connexion (`admin` par défaut).
4. Facultatif, pour ne pas ressaisir les sources après chaque déploiement : `FT_CLIENT_ID`,
   `FT_CLIENT_SECRET`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `IMAP_HOST`, `IMAP_USER`,
   `IMAP_PASSWORD`, `IMAP_SENDERS`, `IMAP_DAYS`. Autres réglages : `CLAUDE_CLI_MODEL`, `CLAUDE_CLI_MODEL_ECO`,
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

L'agent chef appelle les routes `/api/` de l'agent emploi avec l'en-tête
`Authorization: Bearer <HUB_TOKEN>` (la valeur doit être identique des deux côtés ; espaces et
retours à la ligne autour du jeton sont ignorés). En cas de refus, la réponse 401 précise la cause :
`HUB_TOKEN` non configuré chez l'agent emploi, jeton différent, ou jeton vide. Sans en-tête
`Authorization: Bearer`, c'est la connexion par mot de passe habituelle qui s'applique.

## À savoir

- Chaque appel à Claude prend de 10 secondes à 2 minutes et consomme ton quota d'abonnement.
  La notation traite 8 annonces par appel, 40 par clic au maximum.
- Tout est stocké dans `data.sqlite3`, à côté du code, y compris tes identifiants en clair.
  Ne partage pas ce fichier et ne le commite pas (il est dans `.gitignore`).
- En local, le serveur n'écoute que sur 127.0.0.1. Sur Render, tout passe par la page de connexion.
- L'agent a pour consigne de ne rien inventer, mais relis toujours CV et lettre avant envoi.
- Statut « Postulée » : l'étiquette « À relancer » apparaît au bout de 7 jours.

## Tests

    python -m unittest discover -p 'test_*.py'

`test_hub_auth.py` : authentification du lien avec l'agent chef. `test_cycle.py` : préfiltre, garde des
2 jours, sélection limitée à 2 offres, questions fermées et nombre d'appels au modèle par passage.

## Fichiers

- `app.py` : serveur et routes — `hub.py` : lien avec l'agent chef — `agent.py` : mission et prompts (tri, rédaction, coaching)
- `cycle.py` : le passage tous les 2 jours (préfiltre, notation, sélection, questions fermées)
- `cvimport.py` : import des CV et profil automatique — `sources.py` : collecte — `llm.py` : appel à Claude Code — `db.py` : base SQLite
- `static/` : interface
