"""Materialize private React dependencies from a per-ledger npm ci snapshot."""

import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from . import dependency_integrity
from .state import pack


COPY_ON_WRITE = sys.platform == "darwin"
DEPENDENCY_HOOKS = {"preinstall", "install", "postinstall"}
PROJECT_HOOKS = DEPENDENCY_HOOKS | {
    "prepublish", "preprepare", "prepare", "postprepare", "predependencies", "dependencies", "postdependencies",
}

# These exact tarballs from the RTL template leave no build inputs outside node_modules.
# esbuild overrides and forced native compilation are screened separately.
RELOCATABLE_INSTALL_HOOKS = {
    "sha512-ceqxoedUrcayh7Y7ZX6NdbbDzGROiyVBgC4PriJThBKSVPWnnFHZAkfI1lJT8QFkOwH4qOS2SJkS4wvpGl8BpA==",  # esbuild 0.18.20
    "sha512-HKVLS8dvII+xoKW9kmqxbRKrnWEXfJJr/FZhhJmiqIB0e053QNYFqOBouTMO/k5sID4MvCiUCvv8b9M4h32wIA==",  # esbuild 0.28.2
    "sha512-7FNeNl8NCE7aINx7WXiKQrPYZWC/hvrTsmk6zmxbI7LTXE7hVek/n8AfVgpe2y82zl3w0HvCHN0bVKMBoJcC0w==",  # @parcel/watcher
    "sha512-aDdvlDder8QmY91H88GzNi9EtQi2TjvQhpCX6B1v/dAZHU1AuLgHvRh54RiOerpEhEW46Tkf+vgAViB/CWC0ag==",  # core-js
    "sha512-NE4SFDJ+qzQeqHG2vVSNwopzS6zZNPYJ5kinIqs+OofTqGzOmICgT89E/yvS9XY7rwb3eU1utWXTJ9nVFqv74A==",  # core-js-pure
    "sha512-5xoDfX+fL7faATnagmWPpbFtwh/R77WmMMqqHGS65C3vvB0YHrgF+B1YmZ3441tMj5n63k0212XNoJwzlhffQw==",  # fsevents
}


def _registry_package(name, package):
    if not name.startswith("node_modules/") or not isinstance(package, dict) or package.get("link"):
        return False
    path = PurePosixPath(name)
    if path.as_posix() != name or ".." in path.parts or "\\" in name:
        return False
    resolved = package.get("resolved")
    integrity = package.get("integrity")
    if not isinstance(resolved, str) or not isinstance(integrity, str) or not integrity:
        return False
    url = urlsplit(resolved)
    return (
        url.scheme == "https" and bool(url.hostname) and url.path.endswith(".tgz")
        and (not package.get("hasInstallScript") or integrity in RELOCATABLE_INSTALL_HOOKS)
    )


def _package_digest(source):
    if any((source / name).exists() for name in (".npmrc", "npm-shrinkwrap.json", "binding.gyp")):
        return None
    package = (source / "package.json").read_bytes()
    manifest = json.loads(package)
    lock = json.loads((source / "package-lock.json").read_bytes())
    if not isinstance(manifest, dict) or not isinstance(lock, dict):
        return None
    scripts = manifest.get("scripts", {})
    packages = lock.get("packages")
    if manifest.get("workspaces") or not isinstance(scripts, dict) or PROJECT_HOOKS.intersection(scripts):
        return None
    if lock.get("lockfileVersion") not in (2, 3) or not isinstance(packages, dict) or "" not in packages:
        return None
    if any(not _registry_package(name, item) for name, item in packages.items() if name):
        return None
    return hashlib.sha256(package).hexdigest()


def _runtime_identity(source, env):
    if env.get("NODE_OPTIONS") or env.get("ESBUILD_BINARY_PATH") or any(
        name.lower() == "npm_config_build_from_source" and value.lower() not in {"", "false", "0"}
        for name, value in env.items()
    ):
        return None
    node = subprocess.check_output(
        ["node", "-p", "JSON.stringify([process.execPath, process.version, process.platform, "
         "process.arch, process.versions.modules, process.versions.napi])"],
        cwd=source, env=env, timeout=15, stderr=subprocess.DEVNULL,
    )
    npm = subprocess.check_output(
        ["npm", "--version"], cwd=source, env=env, timeout=15, stderr=subprocess.DEVNULL,
    )
    config = json.loads(subprocess.check_output(
        ["npm", "config", "list", "--json"], cwd=source, env=env, timeout=15, stderr=subprocess.DEVNULL,
    ))
    if config.get("global") or config.get("build-from-source") or config.get("script-shell") or config.get("node-options"):
        return None
    # Cross-target installs can resolve platform and libc independently of the Node
    # running npm. Keep their ordinary install path until the cache models them.
    if any(config.get(field) for field in ("os", "cpu", "libc")):
        return None
    omit, include = config.get("omit", []), config.get("include", [])
    if not all(isinstance(flags, list) and all(isinstance(flag, str) for flag in flags) for flags in (omit, include)):
        return None
    return {
        "node": json.loads(node),
        "npm": npm.decode().strip(),
        "system_release": os.uname().release,
        "config_sha256": hashlib.sha256(pack(config).encode()).hexdigest(),
        "omit": sorted(set(omit) - set(include) - {"dev"}),  # RTL always installs dev dependencies.
    }


def _cache_identity(source, lock_digest, env):
    try:
        package_digest = _package_digest(source)
        runtime = _runtime_identity(source, env) if package_digest else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if runtime:
        key = hashlib.sha256(pack({
            "format": 3, "lock": lock_digest, "package": package_digest, "runtime": runtime,
        }).encode()).hexdigest()
        return key, runtime
    return None


def _platform_excludes(value, constraints):
    constraints = [constraints] if isinstance(constraints, str) else constraints
    if not isinstance(constraints, list) or not constraints or not all(isinstance(item, str) for item in constraints):
        return False
    if value is None:
        return True
    if constraints == ["any"]:
        return False
    allowed = [item for item in constraints if not item.startswith("!")]
    return f"!{value}" in constraints or bool(allowed and value not in allowed)


def _intentionally_omitted(package, runtime):
    omit = runtime["omit"]
    if package.get("peer") is True and "peer" in omit:
        return True
    if package.get("optional") is not True:
        return False
    platform, arch = runtime["node"][2:4]
    return (
        "optional" in omit or _platform_excludes(platform, package.get("os"))
        or _platform_excludes(arch, package.get("cpu"))
        or (platform == "darwin" and _platform_excludes(None, package.get("libc")))
    )


def _verified_install(source, lock_digest, runtime):
    lock = (source / "package-lock.json").read_bytes()
    if hashlib.sha256(lock).hexdigest() != lock_digest:
        return False
    for name, package in json.loads(lock)["packages"].items():
        if not name:
            continue
        installed = source / name
        if not installed.exists():
            if not _intentionally_omitted(package, runtime):
                return False
            continue
        if package["integrity"] in RELOCATABLE_INSTALL_HOOKS:
            continue
        manifest = json.loads((installed / "package.json").read_bytes())
        if not isinstance(manifest, dict) or not isinstance(manifest.get("scripts", {}), dict):
            return False
        if DEPENDENCY_HOOKS.intersection(manifest.get("scripts", {})) or (installed / "binding.gyp").exists():
            return False
    return True


def _copy_modules(source, destination):
    # The cache only passes whole, private trees with internal relative links.
    # APFS can duplicate such a tree with copy-on-write data and distinct inodes.
    clonefile = getattr(ctypes.CDLL(None), "clonefile", None)
    if clonefile is not None:
        clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        clonefile.restype = ctypes.c_int
        if clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0:
            return
    raise OSError("could not clone npm dependency tree")


def _open_lock(root, key):
    if root.resolve() != root:
        raise OSError("npm dependency cache must not use a symlink")
    root.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(root / f"{key}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(descriptor, "rb")
    try:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise OSError("npm dependency lock must be a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
    except OSError:
        lock.close()
        raise
    return lock


def _discard_copy(modules):
    if modules.is_symlink():
        modules.unlink()
    elif modules.exists():
        shutil.rmtree(modules)


def _use_cache(root, key, source, lock_digest, runtime, install):
    entry = root / key
    snapshot = entry / "node_modules"
    modules = source / "node_modules"
    integrity = root.with_name("npm-dependency-integrity")
    anchor = integrity / f"{key}.sha256"
    try:
        lock = _open_lock(root, key)
    except (OSError, RuntimeError):
        install()
        return {"mode": "uncached"}

    with lock:
        try:
            if integrity.resolve() != integrity:
                raise OSError("npm integrity directory must not use a symlink")
            integrity.mkdir(mode=0o700, exist_ok=True)
            expected = dependency_integrity.load(anchor)
        except (OSError, RuntimeError):
            install()
            return {"mode": "uncached"}
        if expected and not entry.is_symlink() and not snapshot.is_symlink() and snapshot.is_dir():
            try:
                _copy_modules(snapshot, modules)
            except OSError:
                _discard_copy(modules)
                install()
                return {"mode": "uncached"}
            try:
                if dependency_integrity.tree_sha256(modules) == expected:
                    return {"mode": "cache-hit", "key": key, "tree_sha256": expected}
            except (OSError, RuntimeError, ValueError):
                pass
            _discard_copy(modules)
        install()
        try:
            if not _verified_install(source, lock_digest, runtime):
                return {"mode": "uncached"}
            tree = dependency_integrity.tree_sha256(modules)
            with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as work:
                ready = Path(work) / "ready"
                ready.mkdir(mode=0o700)
                _copy_modules(modules, ready / "node_modules")
                if entry.exists() or entry.is_symlink():
                    entry.rename(Path(work) / "invalid")
                dependency_integrity.publish(anchor, tree)
                ready.rename(entry)
        except (OSError, RuntimeError, ValueError):
            return {"mode": "uncached"}
        return {"mode": "installed", "key": key, "tree_sha256": tree}


def prepare(source, root, lock_digest, env, install):
    """Run npm ci or copy a complete snapshot before invoking the project build."""
    identity = _cache_identity(source, lock_digest, env) if COPY_ON_WRITE else None
    if identity is None:
        install()
        return {"mode": "uncached"}
    key, runtime = identity
    return _use_cache(root, key, source, lock_digest, runtime, install)
