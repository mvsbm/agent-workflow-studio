import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Self


class PostgresFixture:
    def __init__(self) -> None:
        self.image = (Path(__file__).parent / "postgres-image.txt").read_text().strip()
        self.name = "studio-pg-" + uuid.uuid4().hex
        self.password = secrets.token_urlsafe(48)
        self.port = 0
        self.container_id: str | None = None
        self.created = False

    def docker(self, arguments: list[str], timeout: int = 90) -> str:
        try:
            result = subprocess.run(
                ["docker", *arguments],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("postgres_fixture_docker_unavailable") from None
        if result.returncode:
            raise RuntimeError("postgres_fixture_docker_failed")
        return result.stdout.strip()

    def connect_arguments(self) -> dict[str, Any]:
        return {
            "host": "127.0.0.1",
            "port": self.port,
            "dbname": "studio_fixture",
            "user": "studio_fixture",
            "password": self.password,
            "connect_timeout": 2,
        }

    def wait_ready(self) -> None:
        import psycopg

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(**self.connect_arguments()):
                    return
            except psycopg.OperationalError:
                time.sleep(0.25)
        raise RuntimeError("postgres_fixture_not_ready") from None

    def __enter__(self) -> Self:
        if os.environ.get("COMFYUI_POSTGRES_TEST_ALLOWED") != "1":
            raise RuntimeError("postgres_fixture_approval_required")
        if not re.fullmatch(r"postgres@sha256:[a-f0-9]{64}", self.image):
            raise RuntimeError("postgres_fixture_image_invalid")
        self.docker(["info", "--format", "{{.ServerVersion}}"], timeout=10)
        try:
            with tempfile.TemporaryDirectory(prefix="studio-pg-env-") as directory:
                path = Path(directory) / "credentials.env"
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    stream.write(
                        "POSTGRES_USER=studio_fixture\nPOSTGRES_DB=studio_fixture\nPOSTGRES_PASSWORD="
                        + self.password
                        + "\n"
                    )
                self.created = True
                self.container_id = self.docker(
                    [
                        "run",
                        "--detach",
                        "--name",
                        self.name,
                        "--env-file",
                        str(path),
                        "--publish",
                        "127.0.0.1::5432",
                        self.image,
                    ]
                )
            if not re.fullmatch(r"[a-f0-9]{64}", self.container_id):
                raise RuntimeError("postgres_fixture_container_invalid")
            self.refresh_binding()
            self.wait_ready()
            return self
        except Exception:
            self.cleanup()
            raise

    def refresh_binding(self) -> None:
        if not self.created or self.container_id is None:
            raise RuntimeError("postgres_fixture_not_owned")
        ports = json.loads(
            self.docker(
                [
                    "inspect",
                    "--format",
                    "{{json .NetworkSettings.Ports}}",
                    self.container_id,
                ]
            )
        )
        bindings = ports.get("5432/tcp", [])
        if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            raise RuntimeError("postgres_fixture_binding_invalid")
        self.port = int(bindings[0]["HostPort"])
        if not 1 <= self.port <= 65535:
            raise RuntimeError("postgres_fixture_binding_invalid")

    def restart(self) -> None:
        if not self.created or self.container_id is None:
            raise RuntimeError("postgres_fixture_not_owned")
        self.docker(["restart", "--time", "1", self.container_id])
        self.refresh_binding()
        self.wait_ready()

    def cleanup(self) -> None:
        if self.created:
            self.docker(["rm", "--force", "--volumes", self.name])
            self.created = False

    def __exit__(self, *_: object) -> None:
        self.cleanup()
