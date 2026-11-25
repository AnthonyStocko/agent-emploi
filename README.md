# Agent emploi

## Mission

T'aider dans ta recherche d'emploi : chercher des annonces, les noter selon ton profil, rédiger un CV
et une lettre adaptés à chacune, suivre tes candidatures et te faire passer des entretiens simulés à la voix.

Les lundis, mercredis et vendredis, il travaille seul : il te présente 1 ou 2 offres très ciblées, chacune avec un CV et
une lettre prêts à relire, et te pose 1 ou 2 questions fermées pour mieux choisir la fois suivante.
Mieux vaut aucune offre qu'une offre moyenne. Sa consigne complète est `MISSION` dans `agent.py`.

## Ce qu'il fait

| Domaine | Ce qu'il fait |
|---|---|
| Profil | Importe tes CV (PDF, DOCX, ODT, TXT, ou une archive .zip de 40 CV), les fusionne en CV maître, en déduit les postes à viser et le lieu, et garde une fiche d'angle par CV |
| Annonces | Collecte sur France Travail, Adzuna, dans tes alertes mail et à partir d'une annonce collée ; note chaque annonce selon ton profil |
| Sélection | Lundi, mercredi, vendredi : collecte, préfiltre sans modèle (déjà vues, mots exclus, zone, contrat), une passe de notation (24 annonces au plus), puis au plus 2 offres notées 80 ou plus, avec CV et lettre ; sinon, deux lignes sur ce qui manquait |
| Questions | 1 ou 2 questions fermées par sélection (2 nouvelles par jour au plus, jamais reposées), à réponse oui / non ou à choix ; tes réponses orientent les sélections suivantes |
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
  routes `/api/` avec `HUB_TOKEN` : dans la discussion, et chaque matin en semaine pour collecter
  les offres et, trois fois par semaine, lancer la sélection. En retour, l'agent emploi lit la mémoire commune de l'agent chef et lui signale tes
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

## La sélection (lundi, mercredi, vendredi)

Le point du matin de l'agent chef lance `POST /api/selection` les lundis, mercredis et vendredis ; les
autres jours, il ne fait que collecter (`POST /api/collect`, sans notation). Le bouton « Lancer une
sélection maintenant » fait la même chose. Une sélection coûte au plus 5 appels au modèle : une notation
et les questions au modèle économique (`CLAUDE_CLI_MODEL_ECO`, `haiku` par défaut), la sélection et au
plus 2 rédactions au modèle principal (`CLAUDE_CLI_MODEL`, sinon celui de Claude Code). Il ne rédige
jamais pour une offre non retenue. Chaque sélection est enregistrée dans la table `selections` (date,
offres retenues, motif si aucune).

**Préfiltre, sans modèle, avant toute notation** : annonces déjà vues (même intitulé et même entreprise
qu'une annonce connue, vue, écartée ou postulée), mots exclus, zone et, s'ils sont remplis, types de
contrat acceptés (onglet Profil). La zone est le réglage « Zone acceptée », appliqué à toutes les sources.
S'il est vide, la zone est celle des recherches : France Travail cherche dans le département, Adzuna autour
de la ville dans un rayon de 30 km (réglage « Rayon », `rayon_km`), et leurs annonces ne sont pas
recontrôlées ; les alertes mail et les annonces collées sont contrôlées sur le département et la ville.
Adzuna écrit ses lieux « Saint-Étienne, Loire », sans numéro de département : une zone explicite doit donc
citer des communes. Une annonce sans lieu passe le préfiltre, mais la notation ne lui
donne pas 80 si le texte ne dit pas où est le poste, et met sous 50 une annonce hors zone. Au moment de
la sélection, les offres déjà notées repassent le préfiltre (réglages modifiés, offres notées avant
lui). Les annonces écartées passent au statut « Écartée » avec la raison « Préfiltre : … ».

**Questions fermées** : 2 nouvelles par jour au plus, jamais reposées. Elles partent de ce qui l'a fait
hésiter, des incohérences entre tes CV (`a_verifier`, relevées à l'import) et des critères absents. Tu
réponds en un clic dans cette page ou dans le point du matin de l'agent chef. Chaque réponse est
envoyée à l'agent chef (`reponse_profil`) pour l'espace emploi de sa mémoire, et relue avant chaque
notation, sélection et rédaction.

**Archive de CV** : l'import accepte une archive .zip de 40 CV au plus. Le CV maître est fusionné comme
avant ; chaque fichier garde en plus une fiche d'angle (poste visé, titre utilisé, compétences mises en
avant, date) dans la table `cv_archive`. La rédaction reçoit les 3 fiches les plus proches de l'offre.

**Consommation** : chaque appel au modèle est journalisé dans `llm_calls` (étape, modèle, taille du
prompt et de la réponse, durée), sans aucun contenu, et résumé sur `GET /api/consommation` (jetons
estimés à 4 caractères par jeton). Le budget d'une sélection, en jetons estimés, vient de l'agent chef
(`BUDGET_CYCLE_EMPLOI` chez lui) ou de la variable du même nom ici ; au-delà, la rédaction est sautée et
la sélection le signale.

Routes : `GET /api/selection` (dernière sélection et questions), `POST /api/selection` (`{"budget": n}`
facultatif), `POST /api/questions/<id>/answer` avec `{"reponse": "<une des options>"}`,
`GET /api/consommation`, `GET /api/cv_archive`. Une offre s'ouvre directement avec `/#offre-<id>`.

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
   `IMAP_PASSWORD`, `IMAP_SENDERS`, `IMAP_DAYS`. Autres réglages : `CLAUDE_CLI_MODEL`, `CLAUDE_CLI_MODEL_ECO`, `BUDGET_CYCLE_EMPLOI`,
   `CLI_TIMEOUT_SECONDS`, `APP_SESSION_DAYS`, `APP_SESSION_SECRET`.

**Données** : sur l'offre gratuite, le fichier de la base est effacé à chaque redéploiement et à chaque
mise en veille. Avec `HUB_URL` et `HUB_TOKEN`, l'agent en garde une copie chez l'agent chef
(`coffre.py`) : il la dépose quelques secondes après chaque modification (`POST /agent/coffre`) et la
reprend tout seul au démarrage quand sa base est vide (`GET /agent/coffre`) ; pendant la reprise, les
routes `/api/` répondent 503. La copie contient le CV maître, les réglages, les annonces, les questions
et l'archive de CV, jamais les secrets (identifiants France Travail et Adzuna, mot de passe de la boîte
mail) : mets-les dans les variables d'environnement Render. Tant que la reprise n'a pas réussi, aucune
copie n'est déposée, et l'agent chef refuse qu'une base vide remplace une copie existante. L'onglet
Réglages indique la date de la dernière copie. Restent possibles : un disque Render monté sur `/data`
avec `DATA_DIR=/data` (voir `render.yaml`), et le bouton « Télécharger une sauvegarde ».

Claude Code tourne sans aucun outil, dans un dossier vide, et ne voit que le jeton
d'abonnement : ni le mot de passe de l'appli ni tes identifiants mail.

## Déploiement en conteneur (Coolify sur Oracle Cloud, ou un serveur à la maison)

Le même `Dockerfile` sert ailleurs que sur Render. L'image définit `HEBERGE=1` : l'appli écoute sur
0.0.0.0 et applique les mêmes protections que sur Render (sans `APP_PASSWORD`, toute demande est refusée
avec 503 ; `CLAUDE_CODE_OAUTH_TOKEN` obligatoire ; cookie `Secure`, donc HTTPS devant l'appli). Elle se
construit pour arm64 (VM Oracle) comme pour amd64 (PC) ; le workflow `Image Docker` le vérifie à chaque
push.

1. Dans Coolify : nouvelle application depuis ce dépôt, build « Dockerfile », port 8765, contrôle de
   santé `/healthz`.
2. Variables : les mêmes que sur Render (ci-dessus), plus `DATA_DIR=/data`.
3. Volume persistant monté sur `/data` : la base n'est alors plus effacée à chaque déploiement (la copie
   chez l'agent chef reste un filet de sécurité).

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
- En local, le serveur n'écoute que sur 127.0.0.1. Hébergé (Render ou conteneur), tout passe par la page de connexion.
- L'agent a pour consigne de ne rien inventer, mais relis toujours CV et lettre avant envoi.
- Statut « Postulée » : l'étiquette « À relancer » apparaît au bout de 7 jours.

## Tests

    python -m unittest discover -p 'test_*.py'

`test_hub_auth.py` : authentification du lien avec l'agent chef. `test_cycle.py` : préfiltre, plafond
de 2 offres, aucune sélection sous 80, question jamais reposée, budget et journal des appels, import .zip.

## Fichiers

- `app.py` : serveur et routes — `hub.py` : lien avec l'agent chef — `agent.py` : mission et prompts (tri, rédaction, coaching)
- `coffre.py` : copie de la base chez l'agent chef et reprise au démarrage
- `cycle.py` : la sélection (préfiltre, notation, sélection, questions fermées, consommation)
- `cvimport.py` : import des CV et profil automatique — `sources.py` : collecte — `llm.py` : appel à Claude Code — `db.py` : base SQLite
- `static/` : interface
