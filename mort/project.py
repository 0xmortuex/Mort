"""Dependency-free Mort project manifests and source discovery."""
import ast
import glob
import hashlib
import json
import os
import re
import subprocess


class ProjectError(Exception):
    pass


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _parse_value(text, path, line):
    if text == "true":
        return True
    if text == "false":
        return False
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        raise ProjectError(f"{path}:{line}: invalid manifest value {text!r}")
    if not isinstance(value, (str, int, bool, list)):
        raise ProjectError(f"{path}:{line}: unsupported manifest value")
    if isinstance(value, list) and not all(isinstance(item, str) for item in value):
        raise ProjectError(f"{path}:{line}: manifest lists may contain only strings")
    return value


def load_manifest(path):
    """Read the small, stable TOML subset used by ``mort.toml``.

    Sections, strings, booleans, integers, and string arrays are supported.
    Keeping this parser intentionally narrow preserves Python 3.8 support
    without making Mort depend on a third-party TOML package.
    """
    data = {}
    section = None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as error:
        raise ProjectError(f"cannot read manifest {path!r}: {error}")
    for number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("[") and text.endswith("]"):
            name = text[1:-1].strip()
            if not name or not all(part.replace("_", "").isalnum() for part in name.split(".")):
                raise ProjectError(f"{path}:{number}: invalid section name")
            section = data.setdefault(name, {})
            continue
        if section is None or "=" not in text:
            raise ProjectError(f"{path}:{number}: expected a section or key = value")
        key, value_text = (part.strip() for part in text.split("=", 1))
        if not key.replace("_", "").isalnum() or key in section:
            raise ProjectError(f"{path}:{number}: invalid or duplicate key {key!r}")
        section[key] = _parse_value(value_text, path, number)

    package = data.get("package")
    if not package or not isinstance(package.get("name"), str):
        raise ProjectError(f"{path}: [package] must define a string name")
    if not _NAME_RE.match(package["name"]):
        raise ProjectError(f"{path}: invalid package name {package['name']!r}")
    return data


def find_manifest(start="."):
    """Find ``mort.toml`` at ``start`` or in its parent directories."""
    current = os.path.abspath(start)
    if os.path.isfile(current):
        if os.path.basename(current) != "mort.toml":
            raise ProjectError(f"expected a mort.toml file, got {start!r}")
        return current
    while True:
        candidate = os.path.join(current, "mort.toml")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            raise ProjectError(f"no mort.toml found from {os.path.abspath(start)!r}")
        current = parent


def _string_list(section, key, default, manifest_path):
    value = section.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProjectError(f"{manifest_path}: {key!r} must be an array of strings")
    return value


def resolve_project(manifest_path, _seen=None):
    """Return normalized build settings with absolute file paths."""
    manifest_path = os.path.abspath(manifest_path)
    if _seen is None:
        _seen = set()
    key = os.path.normcase(os.path.realpath(manifest_path))
    if key in _seen:
        raise ProjectError(f"dependency cycle involving {manifest_path!r}")
    _seen.add(key)
    root = os.path.dirname(manifest_path)
    data = load_manifest(manifest_path)
    package = data["package"]
    build = data.get("build", {})
    patterns = _string_list(build, "sources", ["src/**/*.mx"], manifest_path)
    sources = []
    for pattern in patterns:
        matches = glob.glob(os.path.join(root, pattern), recursive=True)
        sources.extend(path for path in matches if os.path.isfile(path))
    sources = sorted(dict.fromkeys(os.path.abspath(path) for path in sources))
    if not sources:
        raise ProjectError(f"{manifest_path}: no Mort sources matched {patterns!r}")

    output = build.get("output", os.path.join("build", package["name"]))
    if not isinstance(output, str) or not output:
        raise ProjectError(f"{manifest_path}: 'output' must be a non-empty string")
    if os.name == "nt" and not output.lower().endswith(".exe"):
        output += ".exe"

    dependencies = data.get("dependencies", {})
    packages = {}
    dependency_manifests = []
    for alias, dependency_path in dependencies.items():
        if not _NAME_RE.match(alias) or not isinstance(dependency_path, str):
            raise ProjectError(
                f"{manifest_path}: dependencies must map names to path or git strings")
        if dependency_path.startswith("git+"):
            specification = dependency_path[4:]
            if "#" in specification:
                url, revision = specification.rsplit("#", 1)
            else:
                url, revision = specification, None
            dependency_root = os.path.join(root, ".mort", "deps", alias)
            if not os.path.isdir(os.path.join(dependency_root, ".git")):
                os.makedirs(os.path.dirname(dependency_root), exist_ok=True)
                command = ["git", "clone", "--quiet"]
                if revision:
                    command += ["--branch", revision, "--depth", "1"]
                command += [url, dependency_root]
                try:
                    subprocess.run(command, check=True, capture_output=True, text=True)
                except (OSError, subprocess.CalledProcessError) as error:
                    detail = getattr(error, "stderr", "") or str(error)
                    raise ProjectError(
                        f"failed to fetch dependency {alias!r}: {detail.strip()}")
        else:
            dependency_root = os.path.abspath(os.path.join(root, dependency_path))
        dependency_manifest = find_manifest(dependency_root)
        dependency = resolve_project(dependency_manifest, set(_seen))
        dependency_data = load_manifest(dependency_manifest)
        entry_value = dependency_data.get("build", {}).get("entry")
        if entry_value is None:
            entry = dependency["sources"][0]
        elif isinstance(entry_value, str):
            entry = os.path.abspath(os.path.join(dependency["root"], entry_value))
            if not os.path.isfile(entry):
                raise ProjectError(
                    f"{dependency_manifest}: dependency entry {entry_value!r} does not exist")
        else:
            raise ProjectError(f"{dependency_manifest}: 'entry' must be a string")
        packages[alias] = entry
        for nested_alias, nested_entry in dependency["packages"].items():
            if nested_alias in packages and packages[nested_alias] != nested_entry:
                raise ProjectError(f"dependency alias collision for {nested_alias!r}")
            packages[nested_alias] = nested_entry
        dependency_manifests.extend([dependency_manifest, *dependency["dependency_manifests"]])

    result = {
        "name": package["name"],
        "root": root,
        "sources": sources,
        "output": os.path.abspath(os.path.join(root, output)),
        "std": _string_list(build, "std", [], manifest_path),
        "links": [os.path.abspath(os.path.join(root, item))
                  for item in _string_list(build, "links", [], manifest_path)],
        "libraries": _string_list(build, "libraries", [], manifest_path),
        "tests": _string_list(data.get("test", {}), "sources", ["tests/**/*.mx"], manifest_path),
        "packages": packages,
        "dependency_manifests": sorted(dict.fromkeys(dependency_manifests)),
    }
    _seen.remove(key)
    return result


def resolve_tests(project):
    paths = []
    for pattern in project["tests"]:
        paths.extend(glob.glob(os.path.join(project["root"], pattern), recursive=True))
    return sorted(dict.fromkeys(os.path.abspath(path) for path in paths if os.path.isfile(path)))


def create_project(target):
    """Create a minimal, immediately runnable Mort project."""
    target = os.path.abspath(target)
    name = os.path.basename(target)
    if not _NAME_RE.match(name):
        raise ProjectError(f"invalid project name {name!r}")
    if os.path.exists(target) and os.listdir(target):
        raise ProjectError(f"target directory {target!r} is not empty")
    os.makedirs(os.path.join(target, "src"), exist_ok=True)
    os.makedirs(os.path.join(target, "tests"), exist_ok=True)
    files = {
        os.path.join(target, "mort.toml"): (
            "[package]\n"
            f'name = "{name}"\n'
            'version = "0.1.0"\n\n'
            "[build]\n"
            'sources = ["src/**/*.mx"]\n'
            'std = []\n\n'
            "[test]\n"
            'sources = ["tests/**/*.mx"]\n'
        ),
        os.path.join(target, "src", "main.mx"): (
            "import std.string;\n\n"
            "fn main() -> int {\n"
            f'    println("Hello from {name}!");\n'
            "    return 0;\n"
            "}\n"
        ),
        os.path.join(target, "tests", "smoke.mx"): (
            "import std.string;\n\n"
            'test "string length" {\n'
            "    assert(str_len(\"Mort\") == 4);\n"
            "}\n"
        ),
        os.path.join(target, ".gitignore"): "build/\n.mort/\n*.exe\n*.o\n",
    }
    for path, content in files.items():
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    return target


def add_path_dependency(manifest_path, name, dependency_path):
    """Add a local path dependency while preserving the rest of mort.toml."""
    manifest_path = os.path.abspath(manifest_path)
    project_root = os.path.dirname(manifest_path)
    dependency_manifest = find_manifest(dependency_path)
    dependency_root = os.path.dirname(dependency_manifest)
    relative = os.path.relpath(dependency_root, project_root).replace("\\", "/")
    _append_dependency(manifest_path, name, relative)
    return dependency_manifest


def add_git_dependency(manifest_path, name, url, revision=None):
    """Add a pinned or branch-based Git dependency."""
    if not url:
        raise ProjectError("git dependency URL cannot be empty")
    value = "git+" + url + ("#" + revision if revision else "")
    _append_dependency(os.path.abspath(manifest_path), name, value)
    return value


def _append_dependency(manifest_path, name, value):
    if not _NAME_RE.match(name):
        raise ProjectError(f"invalid dependency name {name!r}")
    data = load_manifest(manifest_path)
    if name in data.get("dependencies", {}):
        raise ProjectError(f"dependency {name!r} is already declared")
    with open(manifest_path, "r", encoding="utf-8") as handle:
        text = handle.read().rstrip() + "\n"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    if "dependencies" in data:
        lines = text.splitlines()
        section_index = next(i for i, line in enumerate(lines)
                             if line.strip() == "[dependencies]")
        insert_at = len(lines)
        for index in range(section_index + 1, len(lines)):
            if lines[index].strip().startswith("["):
                insert_at = index
                break
        lines.insert(insert_at, f'{name} = "{escaped}"')
        text = "\n".join(lines) + "\n"
    else:
        text += f'\n[dependencies]\n{name} = "{escaped}"\n'
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_lockfile(project):
    """Write a deterministic lock snapshot for all local dependencies."""
    packages = []
    for manifest in project["dependency_manifests"]:
        with open(manifest, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        data = load_manifest(manifest)
        package = {
            "name": data["package"]["name"],
            "manifest": os.path.relpath(
                manifest, project["root"]).replace("\\", "/"),
            "sha256": digest,
        }
        dependency_root = os.path.dirname(manifest)
        if os.path.isdir(os.path.join(dependency_root, ".git")):
            try:
                package["revision"] = subprocess.run(
                    ["git", "-C", dependency_root, "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True).stdout.strip()
            except (OSError, subprocess.CalledProcessError):
                pass
        packages.append(package)
    content = {"lock_version": 1, "packages": packages}
    path = os.path.join(project["root"], "mort.lock")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(content, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path
