"""Identity of a relocatable npm tree and its record outside the clone cache."""

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from .state import pack


def tree_sha256(modules):
    """Hash the exact private tree, including paths, modes and relative links."""
    if modules.is_symlink() or not modules.is_dir():
        raise ValueError("npm dependency root is not a directory")
    digest = hashlib.sha256()
    buffer = memoryview(bytearray(256 * 1024))
    directories = [(modules, "")]
    while directories:
        directory, prefix = directories.pop()
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            name = prefix + entry.name
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                value = os.readlink(entry.path)
                if os.path.isabs(value) or not Path(entry.path).resolve().is_relative_to(modules):
                    raise ValueError(f"npm dependency link escapes the private tree: {entry.path}")
            elif stat.S_ISDIR(mode):
                directories.append((entry.path, name + "/"))
                value = None
            elif stat.S_ISREG(mode):
                file_digest = hashlib.sha256()
                with open(entry.path, "rb", buffering=0) as stream:
                    while size := stream.readinto(buffer):
                        file_digest.update(buffer[:size])
                value = file_digest.hexdigest()
            else:
                raise ValueError(f"unsupported npm dependency file: {entry.path}")
            digest.update(pack([name, mode, value]).encode("utf-8", "surrogatepass"))
            digest.update(b"\n")
    return digest.hexdigest()


def load(path):
    if path.is_symlink() or not path.is_file():
        return None
    with path.open("rb") as stream:
        value = stream.read(66)
    return value[:-1].decode("ascii") if re.fullmatch(rb"[0-9a-f]{64}\n", value) else None


def publish(path, digest):
    descriptor, temporary = tempfile.mkstemp(prefix=".digest-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(digest + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
