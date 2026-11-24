"""Cycle autonome : préfiltre, garde des 2 jours, sélection, questions fermées, budget d'appels."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="agent-emploi-test-")
os.environ.pop("HUB_URL", None)

import agent  # noqa: E402
import cycle  # noqa: E402
import db  # noqa: E402
import llm  # noqa: E402

S = {"cv_master": "Chef de projet, 10 ans.", "keywords": "chef de projet", "zone": "69, Lyon",
     "contracts": "CDI", "exclude_words": "ESN"}


def offer(n, **kw):
    o = {"source": "test", "ext_id": str(n), "title": f"Poste {n}", "company": f"Société {n}",
         "location": "69 - Lyon", "contract": "CDI", "description": "Pilotage de projets."}
    o.update(kw)
    db.add_offer(o)
    return db.query("SELECT * FROM offers WHERE ext_id=?", (str(n),))[0]


class Base(unittest.TestCase):
    def setUp(self):
        db.init()
        with db.connect() as con:
            for t in ("offers", "questions", "cycles"):
                con.execute(f"DELETE FROM {t}")


class PrefiltreTest(Base):
    def test_familles_de_contrat(self):
        self.assertEqual(cycle.contract_kind("Contrat à durée indéterminée"), "cdi")
        self.assertEqual(cycle.contract_kind("Contrat à durée déterminée - 6 Mois"), "cdd")
        self.assertEqual(cycle.contract_kind("permanent"), "cdi")
        self.assertEqual(cycle.contract_kind("MIS"), "interim")
        self.assertEqual(cycle.contract_kind(""), "")

    def test_ecarte_zone_contrat_mot_exclu_et_doublon(self):
        keep = offer(1)
        offer(2, location="75 - Paris")
        offer(3, contract="Contrat à durée déterminée")
        offer(4, description="Poste en ESN, mission longue.")
        offer(5, title="Poste 1", company="Société 1", source="autre")
        kept_postal = offer(6, location="69003 Lyon 3e")
        unknown = offer(7, location="", contract="")
        self.assertEqual(cycle.prefilter(S), 4)
        rows = {o["id"]: o for o in db.query("SELECT * FROM offers")}
        for oid in (keep["id"], kept_postal["id"], unknown["id"]):
            self.assertIsNone(rows[oid]["score"])
        out = [o for o in rows.values() if o["score"] == 0]
        self.assertTrue(all(o["status"] == "ecartee" and o["score_reason"].startswith("Préfiltre") for o in out))

    def test_sans_reglage_rien_n_est_ecarte(self):
        offer(1, location="Brest", contract="Stage")
        self.assertEqual(cycle.prefilter({}), 0)


class CycleTest(Base):
    def _fake_llm(self, calls):
        def ask(prompt, timeout=None, model=None):
            calls.append(("ask", model, prompt))
            if "===CV===" in prompt:
                return "===CV===\nCV\n===LETTRE===\nLettre"
            return "Ligne 1\nLigne 2\nLigne 3"

        def ask_json(prompt, timeout=None, model=None):
            calls.append(("json", model, prompt))
            ids = [o["id"] for o in db.query("SELECT id FROM offers ORDER BY id")]
            if "ÉTAPE 3" in prompt:
                return [{"id": i, "score": 90, "raison": "ok"} for i in ids]
            if "ÉTAPE 4" in prompt:  # le modèle en propose 3 : seules 2 sont gardées
                return {"retenues": [{"id": i, "pourquoi": "p", "mise_en_avant": ["a", "b", "c", "d"],
                                      "ecart": "e"} for i in ids], "marche": ""}
            if "ÉTAPE 6" in prompt:
                return [{"question": "PME ou grand groupe ?", "options": ["PME", "grand groupe", "indifférent"]},
                        {"question": "Mauvaise", "options": ["une seule"]},
                        {"question": "Télétravail exigé ?", "options": ["oui", "non"]}]
            raise AssertionError(prompt[:80])
        return ask, ask_json

    def test_passage_complet_puis_garde_des_deux_jours(self):
        for n in (1, 2, 3):
            offer(n)
        calls = []
        ask, ask_json = self._fake_llm(calls)
        with mock.patch.object(llm, "ask", ask), mock.patch.object(llm, "ask_json", ask_json), \
                mock.patch.object(cycle.sources, "collect_all", return_value={}):
            r = cycle.run(S)
            self.assertEqual(r["status"], "fait")
            self.assertEqual(len(r["report"]["selected"]), 2)
            self.assertEqual(len(r["report"]["questions"]), 2)
            # Budget : 1 notation + 1 sélection + 2 rédactions + 1 questions
            self.assertEqual(len(calls), 5)
            eco = [c for c in calls if "ÉTAPE 3" in c[2] or "ÉTAPE 6" in c[2]]
            self.assertTrue(all(c[1] == llm.ECO_MODEL for c in eco))
            self.assertTrue(all(c[1] is None for c in calls if "ÉTAPE 5" in c[2]))
            self.assertIn("#### CV adapté", r["report_md"])
            self.assertIn("**L'écart à préparer :** e", r["report_md"])
            self.assertEqual(r["report_md"].count("\n- a"), 2)
            self.assertNotIn("- d", r["report_md"])
            # Deuxième appel le même jour : rien ne se passe
            calls.clear()
            self.assertEqual(cycle.run(S)["status"], "pas_encore")
            self.assertEqual(calls, [])

    def test_aucune_offre_a_80_aucune_redaction(self):
        offer(1)
        calls = []

        def ask_json(prompt, timeout=None, model=None):
            calls.append(prompt)
            if "ÉTAPE 3" in prompt:
                return [{"id": o["id"], "score": 65, "raison": "moyen"} for o in db.query("SELECT id FROM offers")]
            return []

        def ask(prompt, timeout=None, model=None):
            calls.append(prompt)
            self.assertNotIn("===CV===", prompt)
            return "Peu d'offres en CDI.\nSurtout de l'intérim.\nTroisième ligne ignorée."

        with mock.patch.object(llm, "ask", ask), mock.patch.object(llm, "ask_json", ask_json), \
                mock.patch.object(cycle.sources, "collect_all", return_value={}):
            r = cycle.run(S, force=True)
        self.assertEqual(r["report"]["selected"], [])
        self.assertEqual(r["report"]["market"], "Peu d'offres en CDI.\nSurtout de l'intérim.")
        self.assertEqual(len(calls), 3)  # notation, explication du marché, questions


class QuestionsTest(Base):
    def test_reponse_parmi_les_options_seulement(self):
        qid = db.execute("INSERT INTO questions(text, options) VALUES(?, ?)", ("Télétravail ?", '["oui", "non"]'))
        with self.assertRaises(ValueError):
            cycle.answer(qid, "peut-être")
        self.assertEqual(cycle.answer(qid, "OUI")["answer"], "oui")
        self.assertIn("Télétravail ? → oui", agent.answers_text())

    def test_pas_plus_de_deux_questions_ouvertes(self):
        for t in ("A ?", "B ?"):
            db.execute("INSERT INTO questions(text, options) VALUES(?, ?)", (t, '["oui", "non"]'))
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("aucun appel attendu")):
            self.assertEqual(cycle.ask_questions(S, []), [])



class ModeleTest(unittest.TestCase):
    def test_ask_json_transmet_le_modele(self):
        seen = []

        def fake_run(command, **kw):
            seen.append(command)
            return mock.Mock(stdout='{"result": "[1]"}', stderr="", returncode=0)
        with mock.patch.object(llm.shutil, "which", return_value="claude"), \
                mock.patch.object(llm.subprocess, "run", fake_run), mock.patch.dict(os.environ, {"RENDER": ""}):
            self.assertEqual(llm.ask_json("x", model="haiku"), [1])
        self.assertEqual(seen[0][seen[0].index("--model") + 1], "haiku")


if __name__ == "__main__":
    unittest.main()
