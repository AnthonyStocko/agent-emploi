"""Sélection : préfiltre, plafond de 2 offres, aucune sélection sous 80, questions fermées jamais reposées,
budget du cycle, journal des appels, import d'une archive .zip de CV."""
import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="agent-emploi-test-")
os.environ.pop("HUB_URL", None)

import agent  # noqa: E402
import app  # noqa: E402
import cvimport  # noqa: E402
import cycle  # noqa: E402
import db  # noqa: E402
import hub  # noqa: E402
import llm  # noqa: E402

S = {"cv_master": "Chef de projet, 10 ans.", "keywords": "chef de projet", "zone": "69, Lyon",
     "contracts": "CDI", "exclude_words": "ESN"}


def offer(n, **kw):
    o = {"source": "test", "ext_id": str(n), "title": f"Poste {n}", "company": f"Société {n}",
         "location": "69 - Lyon", "contract": "CDI", "description": "Pilotage de projets."}
    o.update(kw)
    db.add_offer(o)
    return db.query("SELECT * FROM offers WHERE source=? AND ext_id=?", (o["source"], str(n)))[0]


class Base(unittest.TestCase):
    def setUp(self):
        db.init()
        with db.connect() as con:
            for t in ("offers", "questions", "selections", "cv_archive", "llm_calls", "settings"):
                con.execute(f"DELETE FROM {t}")


class PrefiltreTest(Base):
    def test_familles_de_contrat(self):
        self.assertEqual(cycle.contract_kind("Contrat à durée indéterminée"), "cdi")
        self.assertEqual(cycle.contract_kind("Contrat à durée déterminée - 6 Mois"), "cdd")
        self.assertEqual(cycle.contract_kind("permanent"), "cdi")
        self.assertEqual(cycle.contract_kind("MIS"), "interim")
        self.assertEqual(cycle.contract_kind(""), "")

    def test_ecarte_deja_vue_mot_exclu_zone_et_contrat(self):
        keep = offer(1)
        offer(2, location="75 - Paris")
        offer(3, contract="Contrat à durée déterminée")
        offer(4, description="Poste en ESN, mission longue.")
        offer(5, title="Poste 1", company="Société 1", source="autre")       # même offre, autre source
        kept_postal = offer(6, location="69003 Lyon 3e")
        unknown = offer(7, location="", contract="")
        self.assertEqual(cycle.prefilter(S), 4)
        rows = {o["id"]: o for o in db.query("SELECT * FROM offers")}
        for oid in (keep["id"], kept_postal["id"], unknown["id"]):
            self.assertIsNone(rows[oid]["score"])
        out = [o for o in rows.values() if o["score"] == 0]
        self.assertTrue(all(o["status"] == "ecartee" and o["score_reason"].startswith("Préfiltre") for o in out))
        self.assertTrue(any("déjà vue" in o["score_reason"] for o in out))
        self.assertTrue(any("mot exclu « esn »" in o["score_reason"] for o in out))

    def test_deja_postulee_puis_revue_sur_une_autre_source(self):
        old = offer(1)
        db.execute("UPDATE offers SET status='postulee', score=90 WHERE id=?", (old["id"],))
        offer(2, title="Poste 1", company="Société 1", source="mail")
        self.assertEqual(cycle.prefilter({}), 1)

    def test_sans_reglage_rien_n_est_ecarte(self):
        offer(1, location="Brest", contract="Stage")
        self.assertEqual(cycle.prefilter({}), 0)

    def test_zone_par_defaut_departement_et_ville_du_profil(self):
        offer(1, location="75 - Paris")
        lyon = offer(2, location="69 - Villeurbanne")
        offer(3, location="Marseille, Bouches-du-Rhône")
        self.assertEqual(cycle.prefilter({"departement": "69", "location": "Lyon"}), 2)
        self.assertIsNone(db.query("SELECT score FROM offers WHERE id=?", (lyon["id"],))[0]["score"])

    def test_offre_deja_notee_repasse_le_prefiltre_a_la_selection(self):
        paris = offer(1, location="75 - Paris")
        lyon = offer(2)
        db.execute("UPDATE offers SET score=92, score_reason='très bien' WHERE id IN (?, ?)", (paris["id"], lyon["id"]))
        seen = []

        def ask_json(prompt, timeout=None, model=None, step=None):
            seen.append(prompt)
            return {"retenues": [{"id": paris["id"], "pourquoi": "p", "mise_en_avant": [], "ecart": "e"}]}
        with mock.patch.object(llm, "ask_json", ask_json):
            picked, _ = cycle.select({**S, "zone": "", "departement": "69"}, [])
        self.assertEqual(picked, [])                       # l'offre de Paris n'est plus proposée au modèle
        self.assertNotIn("Poste 1", seen[0])
        self.assertIn("Poste 2", seen[0])
        row = db.query("SELECT status, score_reason FROM offers WHERE id=?", (paris["id"],))[0]
        self.assertEqual(row["status"], "ecartee")
        self.assertTrue(row["score_reason"].startswith("Préfiltre : hors zone"))

    def test_la_notation_connait_la_zone(self):
        offer(1)
        seen = []
        with mock.patch.object(llm, "ask_json", lambda p, **k: seen.append(p) or []):
            cycle.score({**S, "zone": "", "departement": "69"}, [db.query("SELECT id FROM offers")[0]["id"]])
        self.assertIn("Zone géographique acceptée : 69.", seen[0])


def fake_llm(calls, score=90, questions=None):
    def run(prompt, timeout=None, model=None):
        calls.append((model, prompt))
        if "ÉTAPE 3" in prompt:
            ids = [o["id"] for o in db.query("SELECT id FROM offers WHERE score IS NULL")]
            return json.dumps([{"id": i, "score": score, "raison": "ok"} for i in ids])
        if "ÉTAPE 4" in prompt:  # le modèle en propose plus de 2 : seules 2 sont gardées
            ids = [o["id"] for o in db.query("SELECT id FROM offers")]
            return json.dumps({"retenues": [{"id": i, "pourquoi": "p", "mise_en_avant": ["a", "b", "c", "d"],
                                             "ecart": "e"} for i in ids], "marche": ""})
        if "ÉTAPE 5" in prompt:
            return "===CV===\nCV\n===LETTRE===\nLettre"
        if "ÉTAPE 6" in prompt:
            return json.dumps(questions if questions is not None else [
                {"question": "PME ou grand groupe ?", "options": ["PME", "grand groupe", "indifférent"]},
                {"question": "Mauvaise", "options": ["une seule"]},
                {"question": "Télétravail exigé ?", "options": ["oui", "non"]}])
        return "Peu d'offres en CDI.\nSurtout de l'intérim.\nTroisième ligne ignorée."
    return run


class SelectionTest(Base):
    """Le modèle est simulé au niveau de l'appel à Claude Code : le journal des appels est réel."""

    def run_selection(self, calls, budget=0, **kw):
        with mock.patch.object(llm, "_run", fake_llm(calls, **kw)), \
                mock.patch.object(cycle.sources, "collect_all", return_value={}):
            return cycle.run(S, budget)

    def test_plafond_de_deux_offres_et_budget_d_appels(self):
        for n in (1, 2, 3):
            offer(n)
        calls = []
        r = self.run_selection(calls)
        self.assertEqual(r["status"], "fait")
        self.assertEqual(len(r["report"]["selected"]), 2)
        self.assertEqual(len(r["report"]["questions"]), 2)
        self.assertEqual(len(calls), 5)  # 1 notation + 1 sélection + 2 rédactions + 1 questions
        steps = [x["etape"] for x in db.query("SELECT etape FROM llm_calls ORDER BY id")]
        self.assertEqual(steps, ["notation", "selection", "redaction", "redaction", "questions"])
        models = {x["etape"]: x["modele"] for x in db.query("SELECT etape, modele FROM llm_calls")}
        self.assertEqual(models["notation"], llm.ECO_MODEL)
        self.assertEqual(models["questions"], llm.ECO_MODEL)
        self.assertNotEqual(models["redaction"], llm.ECO_MODEL)
        sel = cycle.last_selection()
        self.assertEqual(len(sel["offer_ids"]), 2)
        self.assertEqual(sel["motif_si_vide"], "")
        self.assertIn("#### CV adapté", r["report_md"])
        self.assertEqual(r["report_md"].count("\n- a"), 2)
        self.assertNotIn("- d", r["report_md"])

    def test_aucune_selection_sous_80(self):
        offer(1)
        calls = []
        r = self.run_selection(calls, score=79)
        self.assertEqual(r["report"]["selected"], [])
        self.assertFalse(any("ÉTAPE 5" in p for _, p in calls))
        self.assertEqual(cycle.last_selection()["motif_si_vide"], "Peu d'offres en CDI.\nSurtout de l'intérim.")
        self.assertEqual(len(calls), 3)  # notation, explication du marché, questions

    def test_sources_en_erreur_affichees(self):
        report = {"france_travail": {"error": "Identifiants France Travail manquants (onglet Réglages)."},
                  "adzuna": {"found": 0, "new": 0}}
        with mock.patch.object(llm, "_run", fake_llm([])), \
                mock.patch.object(cycle.sources, "collect_all", return_value=report):
            r = cycle.run(S, 0)
        self.assertIn("**Sources en erreur :**\n- France Travail : Identifiants France Travail manquants",
                      r["report_md"])
        self.assertNotIn("Adzuna", r["report_md"])

    def test_budget_depasse_saute_la_redaction(self):
        offer(1)
        calls = []
        r = self.run_selection(calls, budget=1)
        self.assertEqual(len(r["report"]["selected"]), 1)
        self.assertFalse(r["report"]["selected"][0]["documents"])
        self.assertFalse(any("ÉTAPE 5" in p for _, p in calls))
        self.assertEqual(r["report"]["consommation"]["redactions_sautees"], 1)
        self.assertIn("budget du cycle atteint", r["report_md"])

    def test_route_consommation_sans_contenu(self):
        offer(1)
        self.run_selection([])
        c = app.api_get("/api/consommation")
        self.assertGreater(c["jetons_estimes"], 0)
        self.assertEqual({e["etape"] for e in c["etapes"]}, {"notation", "selection", "redaction", "questions"})
        cols = {r["name"] for r in db.query("PRAGMA table_info(llm_calls)")}
        self.assertFalse(cols & {"prompt", "contenu", "reponse"})


class QuestionsTest(Base):
    def test_reponse_parmi_les_options_et_envoi_a_l_agent_chef(self):
        qid = db.execute("INSERT INTO questions(texte, options) VALUES(?, ?)", ("Télétravail ?", '["oui", "non"]'))
        with mock.patch.object(hub, "send_event") as send:
            with self.assertRaises(ValueError):
                cycle.answer(qid, "peut-être")
            self.assertEqual(cycle.answer(qid, "OUI")["reponse"], "oui")
        self.assertEqual(send.call_args.args[0], "reponse_profil")
        self.assertIn("Télétravail ? → oui", agent.answers_text())
        self.assertIn("Télétravail ? → oui", agent._profile(S))  # relue avant notation et sélection

    def test_question_jamais_reposee(self):
        db.execute("INSERT INTO questions(texte, options, reponse, date_pose) VALUES(?, ?, 'oui', '2026-01-01')",
                   ("Télétravail exigé ?", '["oui", "non"]'))
        again = [{"question": "Télétravail  exigé ?", "options": ["oui", "non"]}]
        with mock.patch.object(llm, "_run", fake_llm([], questions=again)):
            self.assertEqual(cycle.ask_questions(S, []), [])
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM questions")[0]["n"], 1)

    def test_pas_plus_de_deux_questions_ouvertes(self):
        for t in ("A ?", "B ?"):
            db.execute("INSERT INTO questions(texte, options) VALUES(?, ?)", (t, '["oui", "non"]'))
        with mock.patch.object(llm, "_run", side_effect=AssertionError("aucun appel attendu")):
            self.assertEqual(cycle.ask_questions(S, []), [])

    def test_incoherences_de_l_archive_dans_les_questions(self):
        calls = []
        with mock.patch.object(llm, "_run", fake_llm(calls, questions=[])):
            cycle.ask_questions({**S, "a_verifier": ["Dates de l'expérience ACME différentes"]}, [])
        self.assertIn("Dates de l'expérience ACME différentes", calls[0][1])


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(zipfile.ZipInfo(name, (2025, 3, 14, 0, 0, 0)), text)
    return base64.b64encode(buf.getvalue()).decode()


class ImportZipTest(Base):
    def test_archive_zip_fiches_d_angle(self):
        files = {"cv-pmo.txt": "PMO senior. " * 30, "dossier/cv-po.md": "Product owner. " * 30,
                 "__MACOSX/._cv-pmo.txt": "x", "photo.jpg": "x"}
        seen = []

        def run(prompt, timeout=None, model=None):
            seen.append(prompt)
            return ("===CV===\n" + "CV maître complet. " * 20 + "\n===PROFIL===\n" + json.dumps({
                "postes": ["PMO"], "resume": "r", "a_verifier": ["Dates ACME"],
                "fiches": [{"fichier": "cv-pmo.txt", "poste_vise": "PMO", "titre": "PMO senior",
                            "competences": ["pilotage", "budget"], "date": ""},
                           {"fichier": "cv-po.md", "poste_vise": "Product owner", "titre": "PO",
                            "competences": ["backlog"], "date": "2024-06"},
                           {"fichier": "inventé.txt", "poste_vise": "x", "titre": "x", "competences": []}]}))
        with mock.patch.object(llm, "_run", run):
            r = cvimport.build_profile({}, [{"name": "archive.zip", "data": zip_of(files)}])
        self.assertEqual(sorted(r["files"]), ["cv-pmo.txt", "cv-po.md"])
        self.assertEqual(r["fiches"], 2)
        rows = {f["fichier"]: f for f in db.query("SELECT * FROM cv_archive")}
        self.assertEqual(rows["cv-pmo.txt"]["date"], "2025-03-14")   # date du fichier dans l'archive
        self.assertEqual(rows["cv-po.md"]["date"], "2024-06")
        self.assertEqual(db.get_settings()["a_verifier"], ["Dates ACME"])
        # Les fiches les plus proches de l'offre vont à la rédaction
        o = offer(1, title="PMO", description="Pilotage et budget du portefeuille.")
        self.assertEqual(agent.angles(o)[0]["fichier"], "cv-pmo.txt")

    def test_archive_trop_grosse_refusee(self):
        files = {f"cv{i}.txt": "x" for i in range(cvimport.MAX_ZIP_FILES + 1)}
        with self.assertRaises(cvimport.ImportError_):
            cvimport.build_profile({}, [{"name": "a.zip", "data": zip_of(files)}])


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
