# Agent emploi

Appli web locale qui cherche des annonces, les note selon ton profil, rédige un CV et une
lettre adaptés, suit tes candidatures et te fait passer des entretiens simulés à la voix.

## Prérequis

- Python 3.9 ou plus récent (aucune bibliothèque à installer).
- Claude Code installé et connecté avec ton abonnement : lance `claude` une fois dans un
  terminal pour vérifier. L'appli appelle `claude -p` en arrière-plan.
- Chrome ou Edge pour la simulation vocale (Firefox ne gère pas la reconnaissance vocale ;
  la saisie au clavier reste possible).

## Lancement

    python app.py

Puis ouvre http://127.0.0.1:8765 (autre port : `python app.py 9000`).

## Mise en route

1. **Profil** : colle ton CV maître complet et tes critères, puis enregistre.
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

## À savoir

- Chaque appel à Claude prend de 10 secondes à 2 minutes et consomme ton quota d'abonnement.
  La notation traite 8 annonces par appel.
- Tout est stocké dans `data.sqlite3`, à côté du code, y compris tes identifiants en clair.
  Ne partage pas ce fichier et ne le commite pas (il est dans `.gitignore`).
- Le serveur n'écoute que sur 127.0.0.1 et refuse les requêtes venant d'autres sites.
- L'agent a pour consigne de ne rien inventer, mais relis toujours CV et lettre avant envoi.
- Statut « Postulée » : l'étiquette « À relancer » apparaît au bout de 7 jours.

## Fichiers

- `app.py` : serveur et routes — `agent.py` : prompts (tri, rédaction, coaching)
- `sources.py` : collecte — `llm.py` : appel à Claude Code — `db.py` : base SQLite
- `static/` : interface
