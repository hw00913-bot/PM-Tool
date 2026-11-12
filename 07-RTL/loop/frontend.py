"""Build and retain offline React assets from an exact source commit."""

import errno
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

from . import npm_dependencies
from .state import guard


PROFILES = ("static", "react")
INSTALL = ("npm", "ci", "--include=dev", "--no-audit", "--no-fund")
BUILD = ("npm", "run", "build")
COMMIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")
SHA256 = re.compile(r"[a-f0-9]{64}")


class _UnverifiedDependencyBuild(ValueError):
    """A build used a dependency snapshot without the required integrity proof."""


def profile(baseline):
    value = baseline.get("frontend", "static")
    if value not in PROFILES:
        raise ValueError(f"未知的前端配置：{value!r}，请选择 static 或 react")
    return value


def _source_commit(repo, commit):
    from .artifacts import rev

    if not isinstance(commit, str) or not COMMIT.fullmatch(commit) or rev(repo, f"{commit}^{{commit}}") != commit:
        raise ValueError("需要完整的 Git 源码提交编号")


def export_source(repo, commit, destination):
    """Extract a checked source commit into a pre-existing empty directory."""
    from .delivery import _extract, _tree_manifest, manifest

    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("源码导出目标必须是空目录")
    _source_commit(repo, commit)
    expected = _tree_manifest(repo, commit)
    _extract(repo, commit, destination)
    if manifest(destination) != expected:
        raise ValueError("导出的源码与 Git 提交不一致，请检查归档属性")
    return expected


def _location(store, run_id, commit):
    from .artifacts import repo_path, run_directory

    run = store.run(run_id)
    if profile(run["baseline"]) != "react":
        raise ValueError("当前运行使用静态前端，无需 React 构建")
    repo = repo_path(store, run_id)
    _source_commit(repo, commit)
    builds = run_directory(store, run_id) / "builds"
    if builds.resolve() != builds or (builds / commit).resolve() != builds / commit:
        raise ValueError("构建目录不能经过符号链接")
    return repo, builds / commit, run["status"] == "active"


def _verified_rebuild(directory):
    result = directory.with_name(f".verified-{directory.name}")
    if result.resolve() != result:
        raise ValueError("构建目录不能经过符号链接")
    return result


def _lock_digest(repo, commit):
    from .artifacts import git

    return hashlib.sha256(git(repo, "show", f"{commit}:package-lock.json")).hexdigest()


class _EntryAssets(HTMLParser):
    def __init__(self, files):
        super().__init__()
        self.files = files

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "base":
            raise ValueError("双击产物应直接使用相对于 index.html 的资源路径")
        if tag == "script":
            if (values.get("type") or "").strip().lower() == "module":
                raise ValueError("双击产物需要普通浏览器脚本，入口不能引用 ES 模块")
            self._check(values.get("src"))
        if tag == "link":
            relation = set((values.get("rel") or "").lower().split())
            if "modulepreload" in relation:
                raise ValueError("双击产物不能预加载 ES 模块")
            if relation.intersection({"stylesheet", "icon", "preload", "manifest"}):
                self._check(values.get("href"))

    def _check(self, url):
        if url is None:
            return
        target = urlsplit(url)
        if target.scheme == "data":
            return
        path = PurePosixPath(unquote(target.path))
        if target.scheme or target.netloc or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"双击产物引用了交付目录之外的资源：{url}")
        if path.as_posix() not in self.files:
            raise ValueError(f"双击产物缺少入口资源：{url}")


def _artifact_manifest(directory):
    from .delivery import manifest

    files = manifest(directory)
    if "index.html" not in files:
        raise ValueError("React 构建缺少 dist/index.html")
    seen = set()
    for name in files:
        parts = PurePosixPath(name).parts
        if (
            name.casefold() in seen
            or any(part.casefold() == ".git" for part in parts)
            or "\\" in name
            or any(ord(character) < 32 for character in name)
        ):
            raise ValueError(f"构建产物含不可交付的文件名：{name}")
        seen.add(name.casefold())
    _EntryAssets(files).feed((directory / "index.html").read_text(encoding="utf-8"))
    return files


def get_build(store, run_id, commit):
    repo, directory, active = _location(store, run_id, commit)
    rebuilt = _verified_rebuild(directory)
    return _read_build(repo, commit, rebuilt if rebuilt.exists() else directory, check_cache=active)


def _read_build(repo, commit, directory, *, check_cache):
    from .state import pack

    receipt_path = directory / "receipt.json"
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError(f"源码提交 {commit[:12]} 缺少已登记的 React 构建，请先执行 build")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("React 构建记录无法读取") from error
    if not isinstance(receipt, dict) or receipt.get("source_commit") != commit:
        raise ValueError("React 构建记录与源码提交不一致")
    if receipt.get("lockfile_sha256") != _lock_digest(repo, commit):
        raise ValueError("React 构建记录与依赖锁文件不一致")
    dependencies = receipt.get("dependency_install")
    cache_hit = isinstance(dependencies, dict) and dependencies.get("mode") == "cache-hit"
    if check_cache and (cache_hit or receipt.get("commands") == [list(BUILD)]):
        digest = dependencies.get("tree_sha256") if cache_hit else None
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise _UnverifiedDependencyBuild("React 构建缺少依赖缓存完整性证明，请先执行 build 重建")
    dist = directory / "dist"
    files = _artifact_manifest(dist)
    if files != receipt.get("manifest"):
        raise ValueError("React 构建产物与登记清单不一致")
    return {
        **receipt, "path": str(dist), "entry": str(dist / "index.html"),
        "asset_digest": hashlib.sha256(pack(files).encode()).hexdigest(),
    }


def _run_npm(command, directory, env, timeout):
    try:
        result = subprocess.run(command, cwd=directory, env=env, capture_output=True, timeout=timeout)
    except FileNotFoundError as error:
        raise ValueError("React 构建需要 Node.js 和 npm") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"React 构建命令超时：{' '.join(command)}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).decode(errors="replace").strip()[-3000:]
        raise ValueError(f"React 构建命令失败：{' '.join(command)}\n{detail}")


def build(store, run_id, commit, *, epoch=None):
    repo, destination, active = _location(store, run_id, commit)
    rebuilt = _verified_rebuild(destination)
    if rebuilt.exists():
        return _read_build(repo, commit, rebuilt, check_cache=active)
    if destination.exists():
        try:
            return _read_build(repo, commit, destination, check_cache=active)
        except _UnverifiedDependencyBuild:
            # Publish beside the previous artifact so a failed build preserves historical evidence.
            destination = rebuilt
    lock_digest = _lock_digest(repo, commit)
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("NODE_ENV", None)
    env.pop("NODE_PATH", None)
    env.setdefault("npm_config_cache", env.get("NPM_CONFIG_CACHE", str(Path(store.root) / "npm-cache")))
    with tempfile.TemporaryDirectory(prefix=".build-", dir=destination.parent) as work:
        source = Path(work) / "source"
        source.mkdir()
        export_source(repo, commit, source)
        if (source / "dist").exists() or (source / "node_modules").exists():
            raise ValueError("React 源码提交应由锁文件重建依赖与 dist 成品")
        dependencies = npm_dependencies.prepare(
            source, store.root / "npm-dependencies", lock_digest, env,
            lambda: _run_npm(INSTALL, source, env, timeout=900),
        )
        _run_npm(BUILD, source, env, timeout=300)
        if hashlib.sha256((source / "package-lock.json").read_bytes()).hexdigest() != lock_digest:
            raise ValueError("构建过程修改了依赖锁文件")
        dist = source / "dist"
        receipt = {
            "source_commit": commit,
            "lockfile_sha256": lock_digest,
            "commands": [list(BUILD)] if dependencies["mode"] == "cache-hit" else [list(INSTALL), list(BUILD)],
            "dependency_install": dependencies,
            "manifest": _artifact_manifest(dist),
        }
        ready = Path(work) / "ready"
        ready.mkdir()
        dist.rename(ready / "dist")
        (ready / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with store.transaction() as db:
            if epoch is not None:
                guard(db, run_id, epoch)
            try:
                ready.rename(destination)
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
    return get_build(store, run_id, commit)
