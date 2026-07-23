"""Dependency-free Mort project manifests and source discovery."""
import ast
import glob
import hashlib
import json
import os
import re
import subprocess
import urllib.request


class ProjectError(Exception):
    pass


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
DEFAULT_REGISTRY_URL = (
    "https://raw.githubusercontent.com/0xmortuex/Mort/main/registry/index.json"
)
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def parse_semver(text):
    """Parse strict SemVer 2.0 into a comparison-friendly tuple."""
    if not isinstance(text, str):
        raise ProjectError(f"semantic version must be a string, got {type(text).__name__}")
    match = _SEMVER_RE.match(text)
    if not match:
        raise ProjectError(f"invalid semantic version {text!r}")
    prerelease = match.group(4)
    identifiers = () if prerelease is None else tuple(prerelease.split("."))
    for identifier in identifiers:
        if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
            raise ProjectError(f"invalid semantic version {text!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), identifiers


def _compare_semver(left, right):
    for index in range(3):
        if left[index] != right[index]:
            return -1 if left[index] < right[index] else 1
    left_pre, right_pre = left[3], right[3]
    if not left_pre or not right_pre:
        return 0 if left_pre == right_pre else (-1 if left_pre else 1)
    for left_id, right_id in zip(left_pre, right_pre):
        if left_id == right_id:
            continue
        left_num, right_num = left_id.isdigit(), right_id.isdigit()
        if left_num and right_num:
            return -1 if int(left_id) < int(right_id) else 1
        if left_num != right_num:
            return -1 if left_num else 1
        return -1 if left_id < right_id else 1
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def semver_satisfies(version, constraint):
    """Return whether a strict version satisfies a compact npm-style range."""
    parsed = parse_semver(version)
    constraint = constraint.strip()
    if constraint in ("", "*"):
        return True
    clauses = [item.strip() for item in constraint.split(",") if item.strip()]
    for clause in clauses:
        if clause.startswith("^"):
            lower = parse_semver(clause[1:])
            if lower[0] > 0:
                upper = (lower[0] + 1, 0, 0, ())
            elif lower[1] > 0:
                upper = (0, lower[1] + 1, 0, ())
            else:
                upper = (0, 0, lower[2] + 1, ())
            if _compare_semver(parsed, lower) < 0 or _compare_semver(parsed, upper) >= 0:
                return False
            continue
        if clause.startswith("~"):
            lower = parse_semver(clause[1:])
            upper = (lower[0], lower[1] + 1, 0, ())
            if _compare_semver(parsed, lower) < 0 or _compare_semver(parsed, upper) >= 0:
                return False
            continue
        wildcard = re.match(r"^(\d+)(?:\.(\d+))?\.(?:x|X|\*)$", clause)
        if wildcard:
            if parsed[0] != int(wildcard.group(1)):
                return False
            if wildcard.group(2) is not None and parsed[1] != int(wildcard.group(2)):
                return False
            continue
        comparison = re.match(r"^(>=|<=|>|<|=)?(.+)$", clause)
        operator = comparison.group(1) or "="
        target = parse_semver(comparison.group(2))
        relation = _compare_semver(parsed, target)
        if not {
            "=": relation == 0,
            ">": relation > 0,
            ">=": relation >= 0,
            "<": relation < 0,
            "<=": relation <= 0,
        }[operator]:
            return False
    return True


def select_semver(versions, constraint):
    matches = [item for item in versions if semver_satisfies(item, constraint)]
    if not matches:
        raise ProjectError(f"no published version satisfies {constraint!r}")
    from functools import cmp_to_key
    return max(matches, key=cmp_to_key(
        lambda left, right: _compare_semver(parse_semver(left), parse_semver(right))))


def _is_semver_constraint(text):
    return bool(text) and (
        text[0] in "^~<>=*" or "," in text
    )


def _resolve_git_semver_tag(url, constraint):
    try:
        output = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", url],
            check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ProjectError(f"failed to list Git versions: {detail.strip()}")
    tags = {}
    for line in output.splitlines():
        reference = line.split("\t", 1)[-1]
        tag = reference.rsplit("/", 1)[-1]
        version = tag[1:] if tag.startswith("v") else tag
        try:
            parse_semver(version)
        except ProjectError:
            continue
        tags[version] = tag
    selected = select_semver(tags, constraint)
    return tags[selected], selected


def _load_registry_index(url, cache_path, offline=False):
    local_source = not url.startswith(("http://", "https://"))
    if not offline or local_source:
        try:
            if url.startswith(("http://", "https://", "file://")):
                with urllib.request.urlopen(url, timeout=15) as response:
                    raw = response.read()
            else:
                with open(os.path.abspath(url), "rb") as handle:
                    raw = handle.read()
            index = json.loads(raw.decode("utf-8"))
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "wb") as handle:
                handle.write(raw)
            return index
        except (OSError, ValueError, urllib.error.URLError) as error:
            online_error = error
    else:
        online_error = None
    try:
        with open(cache_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        if offline:
            raise ProjectError(
                "offline registry resolution needs a cached index or mirror")
        raise ProjectError(f"failed to load registry index {url!r}: {online_error}")


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


def resolve_project(manifest_path, _seen=None, offline=False, mirrors=None):
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

    opt_level = str(build.get("opt_level", "2"))
    if opt_level not in ("0", "1", "2", "3", "s"):
        raise ProjectError(
            f"{manifest_path}: 'build.opt_level' must be 0, 1, 2, 3, or 's'")
    debug = build.get("debug", False)
    if not isinstance(debug, bool):
        raise ProjectError(f"{manifest_path}: 'build.debug' must be a boolean")

    dependencies = data.get("dependencies", {})
    registry = data.get("registry", {})
    registry_url = registry.get(
        "url", os.environ.get("MORT_REGISTRY_URL", DEFAULT_REGISTRY_URL))
    if not isinstance(registry_url, str):
        raise ProjectError(f"{manifest_path}: registry.url must be a string")
    configured_mirrors = list(mirrors or [])
    configured_mirrors.extend(
        item for item in os.environ.get("MORT_MIRRORS", "").split(os.pathsep)
        if item
    )
    configured_mirrors.extend(
        _string_list(registry, "mirrors", [], manifest_path))
    packages = {}
    dependency_manifests = []
    for alias, dependency_path in dependencies.items():
        if not _NAME_RE.match(alias) or not isinstance(dependency_path, str):
            raise ProjectError(
                f"{manifest_path}: dependencies must map names to path or git strings")
        registry_version = None
        if dependency_path.startswith("registry:"):
            request = dependency_path[9:]
            if "@" not in request:
                raise ProjectError(
                    f"{manifest_path}: registry dependency {alias!r} "
                    "must include @version-constraint")
            package_name, constraint = request.rsplit("@", 1)
            index = _load_registry_index(
                registry_url,
                os.path.join(root, ".mort", "registry-index.json"),
                offline=offline,
            )
            versions = (
                index.get("packages", {}).get(package_name, {}).get("versions", {})
            )
            registry_version = select_semver(versions, constraint)
            record = versions[registry_version]
            mirror_root = next((
                os.path.join(os.path.abspath(item), package_name, registry_version)
                for item in configured_mirrors
                if os.path.isfile(os.path.join(
                    os.path.abspath(item), package_name, registry_version, "mort.toml"))
            ), None)
            if mirror_root is not None:
                dependency_path = mirror_root
            else:
                if offline:
                    cached = os.path.join(root, ".mort", "deps", alias)
                    if not os.path.isdir(os.path.join(cached, ".git")):
                        raise ProjectError(
                            f"offline mirror has no {package_name}@{registry_version}")
                git_url = record.get("git")
                if not isinstance(git_url, str):
                    raise ProjectError(
                        f"registry record {package_name}@{registry_version} "
                        "has no Git source")
                dependency_path = (
                    "git+" + git_url + "#"
                    + str(record.get("ref", "v" + registry_version))
                )
        if dependency_path.startswith("git+"):
            specification = dependency_path[4:]
            if "#" in specification:
                url, revision = specification.rsplit("#", 1)
            else:
                url, revision = specification, None
            version_constraint = revision if _is_semver_constraint(revision) else None
            selected_version = None
            clone_revision = revision
            if version_constraint:
                clone_revision, selected_version = _resolve_git_semver_tag(
                    url, version_constraint)
            dependency_root = os.path.join(root, ".mort", "deps", alias)
            if not os.path.isdir(os.path.join(dependency_root, ".git")):
                os.makedirs(os.path.dirname(dependency_root), exist_ok=True)
                command = ["git", "clone", "--quiet"]
                if clone_revision:
                    command += ["--branch", clone_revision, "--depth", "1"]
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
        dependency = resolve_project(
            dependency_manifest, set(_seen), offline=offline,
            mirrors=configured_mirrors)
        dependency_data = load_manifest(dependency_manifest)
        dependency_version = dependency_data["package"].get("version")
        if registry_version is not None and dependency_version != registry_version:
            raise ProjectError(
                f"{dependency_manifest}: registry selected {registry_version} "
                f"but package declares {dependency_version!r}")
        if dependency_path.startswith("git+") and version_constraint:
            if not isinstance(dependency_version, str):
                raise ProjectError(
                    f"{dependency_manifest}: semver dependency must declare "
                    "package.version")
            if not semver_satisfies(dependency_version, version_constraint):
                raise ProjectError(
                    f"{dependency_manifest}: package version {dependency_version!r} "
                    f"does not satisfy {version_constraint!r}")
            if dependency_version != selected_version:
                raise ProjectError(
                    f"{dependency_manifest}: tag resolves as {selected_version} "
                    f"but package declares {dependency_version}")
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
        "opt_level": opt_level,
        "debug": debug,
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


def add_registry_dependency(manifest_path, name, constraint):
    """Add a public-registry dependency with a semantic-version constraint."""
    if not constraint:
        raise ProjectError("registry dependency constraint cannot be empty")
    # Validate the grammar eagerly with a representative search.
    try:
        semver_satisfies("1.0.0", constraint)
    except ProjectError as error:
        raise ProjectError(f"invalid registry version constraint: {error}")
    value = f"registry:{name}@{constraint}"
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


def write_lockfile(project, locked=False):
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
            "content_sha256": _package_content_digest(os.path.dirname(manifest)),
        }
        version = data["package"].get("version")
        if isinstance(version, str):
            parse_semver(version)
            package["version"] = version
        dependency_root = os.path.dirname(manifest)
        if os.path.isdir(os.path.join(dependency_root, ".git")):
            try:
                package["revision"] = subprocess.run(
                    ["git", "-C", dependency_root, "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True).stdout.strip()
            except (OSError, subprocess.CalledProcessError):
                pass
        packages.append(package)
    content = {"lock_version": 3, "packages": packages}
    path = os.path.join(project["root"], "mort.lock")
    if locked:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, ValueError) as error:
            raise ProjectError(f"locked dependency check requires a valid mort.lock: {error}")
        if existing != content:
            raise ProjectError(
                "mort.lock is out of date; run 'mortc fetch' to refresh it")
        return path
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(content, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def _package_content_digest(root):
    """Hash the complete portable package payload, not just its manifest."""
    digest = hashlib.sha256()
    files = []
    for directory, names, filenames in os.walk(root):
        names[:] = [
            name for name in names
            if name not in (".git", ".mort", "build", "__pycache__")
        ]
        for name in filenames:
            if name == "mort.lock" or name.endswith((".pyc", ".o", ".exe")):
                continue
            files.append(os.path.join(directory, name))
    for path in sorted(files, key=lambda item: os.path.relpath(item, root).replace("\\", "/")):
        relative = os.path.relpath(path, root).replace("\\", "/")
        digest.update(relative.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()
