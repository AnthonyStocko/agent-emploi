"""Base durable : copie chez l'agent chef après une écriture, reprise au démarrage, jamais d'écrasement
par une base vide, secrets jamais copiés."""
import base64
import gzip
import os
import tempfile
import unittest
from unittest import mock

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="agent-emploi-test-")

import coffre  # noqa: E402
import db  # noqa: E402
import hub  # noqa: E402


class FauxChef:
    """L'agent chef simulé : garde la dernière copie, refuse une base vide sur une copie existante."""

    def __init__(self, contenu=None, panne=0):
        self.contenu, self.panne, self.depots = contenu, panne, []

    def __call__(self, path, body=None, timeout=45):
        if self.panne:
            self.panne -= 1
            raise OSError("agent chef injoignable")
        if body is None:
            return {"contenu": self.contenu, "updated_at": "2026-11-24T15:00:00+00:00"}
        if body.get("vide") and self.contenu:
            raise ValueError("Base vide refusée")
        self.contenu = body["contenu"]
        self.depots.append(body)
        return {"ok": True}


class CoffreTest(unittest.TestCase):
    def setUp(self):
        db.init()
        with db.connect() as con:
            for t in coffre.TABLES + ["settings"]:
                con.execute(f"DELETE FROM {t}")
        coffre.state.update(ready=False, restoring=False, local_data=None, last_saved=None, error="")
        for p in (mock.patch.object(hub, "HUB_URL", "https://chef"), mock.patch.object(hub, "HUB_TOKEN", "t")):
            p.start()
            self.addCleanup(p.stop)

    def remplir(self):
        db.set_settings({"cv_master": "Mon CV maître", "keywords": "PMO", "ft_client_secret": "SECRET-FT"})
        db.add_offer({"source": "test", "ext_id": "1", "title": "PMO", "company": "ACME"})
        db.execute("INSERT INTO questions(texte, options, reponse) VALUES('PME ?', '[\"oui\",\"non\"]', 'oui')")

    def test_copie_puis_reprise_apres_effacement(self):
        chef = FauxChef()
        with mock.patch.object(hub, "_call", chef):
            self.assertTrue(coffre.restore())          # première fois : rien chez l'agent chef
            self.remplir()
            self.assertTrue(coffre.save())
            # Redéploiement : la base locale est effacée
            with db.connect() as con:
                for t in coffre.TABLES + ["settings"]:
                    con.execute(f"DELETE FROM {t}")
            coffre.state.update(ready=False, local_data=None)
            self.assertTrue(coffre.restore())
        s = db.get_settings()
        self.assertEqual(s["cv_master"], "Mon CV maître")
        self.assertNotIn("ft_client_secret", s)        # les secrets ne voyagent pas
        self.assertEqual(db.query("SELECT title FROM offers")[0]["title"], "PMO")
        self.assertEqual(db.query("SELECT reponse FROM questions")[0]["reponse"], "oui")

    def test_secrets_absents_de_la_copie(self):
        self.remplir()
        texte = gzip.decompress(base64.b64decode(coffre.snapshot())).decode("utf-8")
        self.assertIn("Mon CV maître", texte)
        self.assertNotIn("SECRET-FT", texte)

    def test_pas_de_depot_tant_que_la_reprise_echoue(self):
        chef = FauxChef(contenu=None, panne=10)
        with mock.patch.object(hub, "_call", chef), mock.patch.object(coffre, "schedule"):
            coffre.state["local_data"] = False         # base vide au démarrage
            self.assertFalse(coffre.restore(attempts=2, pause=0))
            db.set_settings({"keywords": "PMO"})       # une écriture pendant la panne
            self.assertFalse(coffre.save())
        self.assertEqual(chef.depots, [])

    def test_base_remplie_apres_une_reprise_ratee_n_ecrase_pas(self):
        """Démarrage à vide, agent chef injoignable, puis une saisie : on ne dépose qu'après une reprise."""
        self.remplir()
        avant = coffre.snapshot()
        with db.connect() as con:
            for t in coffre.TABLES + ["settings"]:
                con.execute(f"DELETE FROM {t}")
        chef = FauxChef(contenu=avant, panne=1)
        with mock.patch.object(hub, "_call", chef), mock.patch.object(coffre, "schedule"):
            coffre.state["local_data"] = False
            self.assertFalse(coffre.restore())          # panne
            db.set_settings({"keywords": "autre"})      # base locale plus vide
            self.assertTrue(coffre.save())              # la reprise passe d'abord, puis le dépôt
        self.assertEqual(db.get_settings()["cv_master"], "Mon CV maître")
        self.assertEqual(db.get_settings()["keywords"], "autre")   # la saisie n'est pas perdue

    def test_profil_sans_cv_garde_apres_redeploiement(self):
        """Profil saisi à la main, sans CV ni annonce : copié à chaque modification, puis repris."""
        chef = FauxChef()
        with mock.patch.object(hub, "_call", chef):
            self.assertTrue(coffre.restore())
            db.set_settings({"keywords": "PMO", "location": "Saint-Étienne"})
            self.assertFalse(coffre.is_empty())
            self.assertTrue(coffre.save())
            db.set_settings({"keywords": "Chef de projet"})
            self.assertTrue(coffre.save())              # pas refusé comme « base vide »
            with db.connect() as con:
                con.execute("DELETE FROM settings")
            coffre.state.update(ready=False, local_data=None)
            self.assertTrue(coffre.restore())
        self.assertEqual(db.get_settings()["keywords"], "Chef de projet")
        self.assertEqual(db.get_settings()["location"], "Saint-Étienne")

    def test_ecriture_programme_un_depot(self):
        with mock.patch.object(coffre, "schedule") as sched:
            db.listeners.append(coffre.schedule)
            try:
                db.set_settings({"keywords": "PMO"})
            finally:
                db.listeners.remove(coffre.schedule)
        sched.assert_called()

    def test_sans_agent_chef_rien_ne_change(self):
        with mock.patch.object(hub, "HUB_URL", ""), mock.patch.object(hub, "_call") as call:
            self.assertFalse(coffre.restore())
            self.assertFalse(coffre.save())
            coffre.start()
        call.assert_not_called()
        self.assertFalse(coffre.state["restoring"])


if __name__ == "__main__":
    unittest.main()
