"""The studio's defaults for ComfyUI's settings fill in only what the user has not set."""

# Verifies: R37
import json
import tempfile
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.studio_defaults import DEFAULTS, apply


class StudioDefaultsTests(unittest.TestCase):
    def test_defaults_fill_in_once_and_never_override_the_user(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "default" / "comfy.settings.json"
            self.assertEqual(apply(file), sorted(DEFAULTS))  # a new user gets them all
            self.assertIn(
                "Comfy.MaskEditor", json.loads(file.read_text())["Comfy.Extension.Disabled"]
            )
            file.write_text(
                json.dumps({"Comfy.Extension.Disabled": [], "Comfy.TutorialCompleted": True})
            )
            self.assertEqual(apply(file), ["Comfy.NodeBadge.NodeSourceBadgeMode"])
            settings = json.loads(file.read_text())
            self.assertEqual(
                settings["Comfy.Extension.Disabled"], []
            )  # the user turned them back on
            self.assertTrue(settings["Comfy.TutorialCompleted"])  # the rest is kept
            file.write_text("{broken")
            self.assertEqual(apply(file), [])  # never overwrites a file it cannot read
            self.assertEqual(file.read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
