import os
import stat
import unittest
from pathlib import Path
from unittest.mock import patch

from postgres_fixture import PostgresFixture


class PostgresFixturePolicyTests(unittest.TestCase):
    def test_missing_approval_never_calls_docker(self) -> None:
        fixture = PostgresFixture()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(fixture, "docker") as docker,
        ):
            with self.assertRaisesRegex(RuntimeError, "approval_required"), fixture:
                pass
            docker.assert_not_called()

    def test_unpinned_image_never_calls_docker(self) -> None:
        fixture = PostgresFixture()
        fixture.image = "postgres:latest"
        with (
            patch.dict(os.environ, {"COMFYUI_POSTGRES_TEST_ALLOWED": "1"}, clear=True),
            patch.object(fixture, "docker") as docker,
        ):
            with self.assertRaisesRegex(RuntimeError, "image_invalid"), fixture:
                pass
            docker.assert_not_called()

    def test_absent_daemon_never_creates_or_removes_container(self) -> None:
        fixture = PostgresFixture()
        with (
            patch.dict(os.environ, {"COMFYUI_POSTGRES_TEST_ALLOWED": "1"}, clear=True),
            patch.object(
                fixture, "docker", side_effect=RuntimeError("daemon_unavailable")
            ) as docker,
        ):
            with self.assertRaisesRegex(RuntimeError, "daemon_unavailable"), fixture:
                pass
            self.assertEqual(docker.call_count, 1)
            self.assertFalse(fixture.created)

    def test_private_credentials_loopback_and_owned_cleanup(self) -> None:
        fixture = PostgresFixture()
        calls = []

        def docker(arguments, timeout=90):
            calls.append(arguments)
            if arguments[0] == "run":
                path = Path(arguments[arguments.index("--env-file") + 1])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertIn(fixture.password, path.read_text())
                self.assertNotIn(fixture.password, str(arguments))
                self.assertIn("127.0.0.1::5432", arguments)
                return "a" * 64
            if arguments[0] == "inspect":
                return '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"32123"}]}'
            return "fixture-output"

        with (
            patch.dict(os.environ, {"COMFYUI_POSTGRES_TEST_ALLOWED": "1"}, clear=True),
            patch.object(fixture, "docker", side_effect=docker),
            patch.object(fixture, "wait_ready"),
            fixture,
        ):
            self.assertEqual(fixture.port, 32123)
        self.assertEqual(calls[-1], ["rm", "--force", "--volumes", fixture.name])
        self.assertFalse(fixture.created)

    def test_bad_binding_cleans_only_owned_container(self) -> None:
        fixture = PostgresFixture()
        with (
            patch.dict(os.environ, {"COMFYUI_POSTGRES_TEST_ALLOWED": "1"}, clear=True),
            patch.object(
                fixture,
                "docker",
                side_effect=["version", "a" * 64, '{"5432/tcp":[]}', "removed"],
            ) as docker,
        ):
            with self.assertRaisesRegex(RuntimeError, "binding_invalid"), fixture:
                pass
            self.assertEqual(
                docker.call_args.args[0], ["rm", "--force", "--volumes", fixture.name]
            )

    def test_restart_refreshes_ephemeral_port_before_waiting(self) -> None:
        fixture = PostgresFixture()
        fixture.created = True
        fixture.container_id = "a" * 64
        fixture.port = 32123
        with (
            patch.object(
                fixture,
                "docker",
                side_effect=[
                    "restarted",
                    '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"32124"}]}',
                ],
            ),
            patch.object(fixture, "wait_ready") as wait,
        ):
            fixture.restart()
            self.assertEqual(fixture.port, 32124)
            wait.assert_called_once()

    def test_restart_requires_owned_container(self) -> None:
        fixture = PostgresFixture()
        with patch.object(fixture, "docker") as docker:
            with self.assertRaisesRegex(RuntimeError, "not_owned"):
                fixture.restart()
            docker.assert_not_called()
