"""Hébergé hors de Render (conteneur avec HEBERGE=1) : mêmes protections que sur Render."""
import http.client
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="agent-emploi-test-"))

import app  # noqa: E402
import llm  # noqa: E402


class HebergeTest(unittest.TestCase):
    def test_drapeau(self):
        with mock.patch.dict(os.environ, {"RENDER": "", "HEBERGE": ""}):
            self.assertFalse(llm.heberge())
        with mock.patch.dict(os.environ, {"RENDER": "true", "HEBERGE": ""}):
            self.assertTrue(llm.heberge())
        with mock.patch.dict(os.environ, {"RENDER": "", "HEBERGE": "1"}):
            self.assertTrue(llm.heberge())

    def test_sans_mot_de_passe_tout_est_refuse(self):
        serveur = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        try:
            env = {"RENDER": "", "HEBERGE": "1", "APP_PASSWORD": "", "HUB_TOKEN": ""}
            with mock.patch.dict(os.environ, env):
                for chemin in ("/", "/api/profile"):
                    c = http.client.HTTPConnection("127.0.0.1", serveur.server_address[1], timeout=5)
                    # même en se faisant passer pour une requête locale
                    c.request("GET", chemin, headers={"Host": "localhost"})
                    self.assertEqual(c.getresponse().status, 503, chemin)
                    c.close()
                c = http.client.HTTPConnection("127.0.0.1", serveur.server_address[1], timeout=5)
                c.request("GET", "/healthz")
                self.assertEqual(c.getresponse().status, 200)
                c.close()
        finally:
            serveur.shutdown()
            serveur.server_close()


if __name__ == "__main__":
    unittest.main()
