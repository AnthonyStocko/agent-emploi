import os
import unittest
from unittest import mock

import app


class HubAuthTest(unittest.TestCase):
    def test_cas(self):
        with mock.patch.dict(os.environ, {"HUB_TOKEN": "secret\n"}):
            self.assertEqual(app.hub_auth("Bearer secret"), "ok")
            self.assertEqual(app.hub_auth("bearer  secret "), "ok")
            self.assertEqual(app.hub_auth("Bearer autre"), "different")
            self.assertEqual(app.hub_auth("Bearer "), "vide")
            self.assertEqual(app.hub_auth(None), "absent")
            self.assertEqual(app.hub_auth("Basic abc"), "absent")
        with mock.patch.dict(os.environ, {"HUB_TOKEN": ""}):
            self.assertEqual(app.hub_auth("Bearer secret"), "non_configure")


if __name__ == "__main__":
    unittest.main()
