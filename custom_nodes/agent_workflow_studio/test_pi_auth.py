import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from . import pi_auth


class PiAuthTests(unittest.TestCase):
    def tearDown(self):
        pi_auth.all_provider_api_key_env_names.cache_clear()
        pi_auth.pi_api_key_providers.cache_clear()
        pi_auth.provider_api_key_fields.cache_clear()
        pi_auth.provider_api_key_envs.cache_clear()

    def test_metadata_probe_uses_pi_metadata_without_inheriting_secrets(self):
        metadata = (
            '{"providers":["sample-provider"],"fields":{"sample-provider":["SAMPLE_API_KEY"]}}'
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=metadata)
        with (
            patch.object(
                pi_auth,
                "_pi_ai_compat_path",
                return_value=Path("/fake/compat.js"),
            ),
            patch.object(pi_auth.shutil, "which", return_value="/usr/bin/node"),
            patch.object(pi_auth.subprocess, "run", return_value=completed) as run,
        ):
            fields = pi_auth.provider_api_key_fields(("sample-provider",))

        self.assertEqual(fields, {"sample-provider": ("SAMPLE_API_KEY",)})
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertIn("sample-provider", run.call_args.args[0][-1])

    def test_save_and_resolve_use_provider_scoped_keychain_accounts(self):
        with (
            patch.object(
                pi_auth,
                "provider_api_key_envs",
                return_value=("SAMPLE_API_KEY",),
            ),
            patch.object(pi_auth, "save_credential") as save,
            patch.object(pi_auth, "get_credential", return_value="not-for-output"),
        ):
            pi_auth.save_provider_credential("sample-provider", "SAMPLE_API_KEY", "secret-value")
            environment = pi_auth.provider_credential_environment("sample-provider")

        save.assert_called_once_with("secret-value", "pi:sample-provider:SAMPLE_API_KEY")
        self.assertEqual(environment, {"SAMPLE_API_KEY": "not-for-output"})
        self.assertEqual(
            pi_auth.credential_account("opencode-go", "OPENCODE_API_KEY"),
            "opencode-go",
        )

    def test_unknown_provider_key_field_is_rejected(self):
        with (
            patch.object(
                pi_auth,
                "provider_api_key_envs",
                return_value=("SAMPLE_API_KEY",),
            ),
            self.assertRaisesRegex(ValueError, "does not report"),
        ):
            pi_auth.save_provider_credential("sample-provider", "OTHER_KEY", "secret-value")


if __name__ == "__main__":
    unittest.main()
