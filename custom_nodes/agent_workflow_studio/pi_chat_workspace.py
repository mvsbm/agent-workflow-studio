"""Persistent workflow data, accessed only through descriptor-relative file IO.

Not a process sandbox: generated files are never executed or discovered as Pi
configuration. The trusted Pi process keeps its original working directory.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import os
import secrets
import stat
import uuid
from pathlib import Path

FILE_ACTIONS = {"workspace_info", "workspace_stat", "workspace_read", "workspace_write"}
# ponytail: bounded files/tree, not an asset store; add streaming/indexed quotas
# when workflows need files beyond 128 KiB or more than 128 entries.
MAX_FILE = 128 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_FILES = 128
MAX_DIRS = 32
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def workflow_id(value):
    if not isinstance(value, str):
        raise TypeError("A native workflow UUID is required.")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as error:
        raise ValueError("A native workflow UUID is required.") from error
    if value != canonical:
        raise ValueError("Use the canonical native workflow UUID.")
    return canonical


def workspace_id_for_graph(value):
    """ComfyUI IDs are opaque strings; only storage folder IDs must be UUIDs."""
    if value is None:
        raise ValueError(
            "Workflow binding is missing. Refresh ComfyUI to load the current Pi chat, then start New chat."
        )
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Load a workflow with a non-empty native graph ID before connecting Pi.")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "comfyui-workflow:" + value))


class PiChatWorkspace:
    def __init__(self, root: Path, id: str):
        self.root = Path(root).absolute()
        self.id = workflow_id(id)
        self.path = self.root / self.id

    @staticmethod
    def _parts(path):
        if not isinstance(path, str) or not path or len(path) > 512:
            raise ValueError("Use a workspace-relative file path (maximum 512 characters).")
        parts = path.split("/")
        if (
            len(parts) > 16
            or "\\" in path
            or any(ord(c) < 32 for c in path)
            or any(p in {"", ".", ".."} for p in parts)
        ):
            raise ValueError("Absolute paths and traversal are not allowed.")
        return parts

    @contextlib.contextmanager
    def _directory(self):
        # The parent is the server-configured user directory, never a model path.
        with contextlib.ExitStack() as stack:
            parent = os.open(self.root.parent, DIR_FLAGS)
            stack.callback(os.close, parent)
            for name in (self.root.name, self.id):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                parent = os.open(name, DIR_FLAGS, dir_fd=parent)
                stack.callback(os.close, parent)
            # The same directory inode serializes cooperating chats/processes.
            fcntl.flock(parent, fcntl.LOCK_EX)
            yield parent

    @contextlib.contextmanager
    def _parent(self, root, parts, *, create=False):
        with contextlib.ExitStack() as stack:
            parent = root
            for name in parts[:-1]:
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                parent = os.open(name, DIR_FLAGS, dir_fd=parent)
                stack.callback(os.close, parent)
            yield parent

    @staticmethod
    def _bytes(parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Workspace files must be regular files, not links/devices.")
            with os.fdopen(fd, "rb", closefd=False) as file:
                data = file.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ValueError("Workspace files are limited to 128 KiB.")
            return data
        finally:
            os.close(fd)

    @staticmethod
    def _tree(root):
        files, directories = [], []

        def visit(fd, prefix=""):
            for name in sorted(os.listdir(fd)):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                path = prefix + name
                if stat.S_ISDIR(info.st_mode):
                    directories.append(path)
                    if len(directories) > MAX_DIRS:
                        raise ValueError("Workspace directory quota exceeded.")
                    child = os.open(name, DIR_FLAGS, dir_fd=fd)
                    try:
                        visit(child, path + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    files.append({"path": path, "size": info.st_size})
                    if len(files) > MAX_FILES:
                        raise ValueError("Workspace file quota exceeded.")
                else:
                    raise ValueError("Links/devices are not allowed in a workspace.")

        visit(root)
        return files, directories

    def perform(self, action, args):
        if action not in FILE_ACTIONS or not isinstance(args, dict):
            raise ValueError("Invalid workspace action.")
        allowed = {
            "workspace_info": set(),
            "workspace_stat": {"path"},
            "workspace_read": {"path"},
            "workspace_write": {"path", "data", "expected_sha256"},
        }[action]
        if set(args) != allowed:
            raise ValueError("Invalid workspace arguments; shell/then_run is not supported.")
        try:
            with self._directory() as root:
                if action == "workspace_info":
                    files, _ = self._tree(root)
                    return {
                        "workflow_id": self.id,
                        "root": str(self.path),
                        "files": files,
                        "max_file_bytes": MAX_FILE,
                        "max_total_bytes": MAX_TOTAL,
                        "shell_enabled": False,
                    }
                parts = self._parts(args["path"])
                try:
                    with self._parent(root, parts) as parent:
                        existing = self._bytes(parent, parts[-1])
                except FileNotFoundError:
                    existing = None
                digest = hashlib.sha256(existing).hexdigest() if existing is not None else None
                if action == "workspace_stat":
                    return {
                        "exists": existing is not None,
                        "sha256": digest,
                        "size": len(existing) if existing is not None else 0,
                    }
                if action == "workspace_read":
                    if existing is None:
                        raise ValueError("Workspace file does not exist.")
                    return {
                        "data": base64.b64encode(existing).decode("ascii"),
                        "sha256": digest,
                    }
                expected = args["expected_sha256"]
                if expected is not None and (
                    not isinstance(expected, str)
                    or len(expected) != 64
                    or any(c not in "0123456789abcdef" for c in expected)
                ):
                    raise ValueError("Invalid expected file hash.")
                if digest != expected:
                    raise ValueError("Workspace file changed; read it again before overwriting.")
                if not isinstance(args["data"], str) or len(args["data"]) > (MAX_FILE + 2) // 3 * 4:
                    raise ValueError("Workspace files are limited to 128 KiB.")
                data = base64.b64decode(args["data"], validate=True)
                if len(data) > MAX_FILE:
                    raise ValueError("Workspace files are limited to 128 KiB.")
                files, directories = self._tree(root)
                parents = {"/".join(parts[:i]) for i in range(1, len(parts))}
                if (
                    len(set(directories) | parents) > MAX_DIRS
                    or len(files) + (existing is None) > MAX_FILES
                ):
                    raise ValueError("Workspace entry quota exceeded.")
                if (
                    sum(f["size"] for f in files)
                    - (len(existing) if existing is not None else 0)
                    + len(data)
                    > MAX_TOTAL
                ):
                    raise ValueError("Workspace storage quota exceeded.")
                with self._parent(root, parts, create=True) as parent:
                    temp = ".pi-write-" + secrets.token_hex(16)
                    try:
                        fd = os.open(
                            temp,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            mode=0o600,
                            dir_fd=parent,
                        )
                        with os.fdopen(fd, "wb") as file:
                            file.write(data)
                            file.flush()
                            os.fsync(file.fileno())
                        os.replace(temp, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                        os.fsync(parent)
                    finally:
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(temp, dir_fd=parent)
                return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        except OSError as error:
            raise ValueError(
                "Workspace IO rejected; paths must not contain links or special files."
            ) from error
