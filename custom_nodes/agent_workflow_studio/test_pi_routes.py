import unittest
from types import SimpleNamespace
from unittest.mock import patch

from . import routes


class PiRouteTests(unittest.TestCase):
    def test_catalog_combines_local_models_and_installed_provider_metadata(self):
        with (
            patch.object(
                routes,
                "list_available_models",
                return_value=[
                    {
                        "provider": "configured-provider",
                        "id": "model-a",
                        "name": "Model A",
                    }
                ],
            ),
            patch.object(
                routes,
                "pi_api_key_providers",
                return_value=("configured-provider", "other-provider"),
            ),
            patch.object(
                routes,
                "provider_api_key_fields",
                return_value={
                    "configured-provider": ("CONFIGURED_API_KEY",),
                    "other-provider": ("OTHER_API_KEY",),
                },
            ),
            patch.object(
                routes,
                "provider_credential_status",
                side_effect=[{"CONFIGURED_API_KEY": True}, {"OTHER_API_KEY": False}],
            ),
        ):
            catalog = routes._collect_pi_catalog()

        self.assertEqual(
            [provider["id"] for provider in catalog["providers"]],
            ["configured-provider", "other-provider"],
        )
        self.assertTrue(catalog["providers"][0]["has_models"])
        self.assertFalse(catalog["providers"][1]["has_models"])
        self.assertIs(catalog["provider_requests_made"], False)
        self.assertNotIn("secret-value", str(catalog))

    def test_loopback_guard_rejects_remote_and_cross_origin_requests(self):
        local = SimpleNamespace(remote="127.0.0.1", headers={}, host="127.0.0.1:8188")
        remote = SimpleNamespace(remote="203.0.113.1", headers={}, host="example.test")
        cross_origin = SimpleNamespace(
            remote="127.0.0.1",
            headers={"Origin": "https://attacker.test"},
            host="127.0.0.1:8188",
        )

        self.assertTrue(routes._is_local_request(local))
        self.assertFalse(routes._is_local_request(remote))
        self.assertFalse(routes._is_local_request(cross_origin))


if __name__ == "__main__":
    unittest.main()
