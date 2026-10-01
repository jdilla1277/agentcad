"""Project discovery and the single boundary between source and build paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

import click

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


class ProjectError(Exception):
    def __init__(
        self, reason: str, message: str, suggestion: str,
        *, next_actions: list[str] | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.suggestion = suggestion
        self.next_actions = next_actions

    def payload(self, command: str) -> dict:
        payload = {
            "command": command,
            "status": "error",
            "reason": self.reason,
            "message": str(self),
            "suggestion": self.suggestion,
        }
        if self.next_actions is not None:
            payload["next_actions"] = self.next_actions
        return payload


@dataclass(frozen=True)
class ProjectLayout:
    project_root: Path
    build_root: Path
    source: str = "default"

    @property
    def manifest_path(self) -> Path:
        return self.build_root / "agentcad.json"

    @property
    def state_dir(self) -> Path:
        return self.artifact_path(".agentcad")

    @property
    def configured(self) -> bool:
        return self.source != "default"

    def describe(self) -> dict:
        return {
            "project_root": str(self.project_root),
            "build_root": str(self.build_root),
            "build_root_source": self.source,
        }

    def artifact_path(self, value: str | Path) -> Path:
        path = (self.build_root / value).resolve()
        if not path.is_relative_to(self.build_root):
            raise ProjectError(
                "artifact_path_escape",
                f"Artifact path escapes build directory: {value}",
                "Use a path inside the selected build directory.",
            )
        return path

    def version_dir(self, entry: dict | str) -> Path:
        return self.artifact_path(entry["path"] if isinstance(entry, dict) else entry)

    def response_path(self, path: Path) -> str:
        path = path.resolve()
        if not self.configured and Path.cwd().resolve() == self.project_root:
            try:
                return path.relative_to(self.project_root).as_posix()
            except ValueError:
                pass
        return str(path)

    def missing_manifest(self) -> ProjectError:
        return ProjectError(
            "build_root_not_initialized",
            f"No AgentCAD manifest at {self.manifest_path}; existing histories were left unchanged.",
            f"Run `agentcad init --build-dir {shlex.quote(str(self.build_root))}` from {self.project_root} to initialize a fresh history.",
            next_actions=[shlex.join(["agentcad", "init", "--build-dir", str(self.build_root)])],
        )

    def read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            raise self.missing_manifest()
        try:
            if self.manifest_path.is_symlink():
                raise ValueError("manifest must not be a symlink")
            data = json.loads(self.manifest_path.read_text())
            if not isinstance(data, dict) or not isinstance(
                data.get("versions", []), list
            ):
                raise ValueError("expected an object with a versions list")
            owner = data.get("project_root")
            if owner is not None:
                if not isinstance(owner, str):
                    raise ValueError("project_root must be a path string")
                if Path(owner).resolve() != self.project_root:
                    raise ProjectError(
                        "invalid_manifest",
                        f"Build directory {self.build_root} belongs to source project {owner}.",
                        f"Run from {owner}, or initialize a separate build directory for this project.",
                    )
            for entry in data.get("versions", []):
                if not isinstance(entry, dict) or not isinstance(
                    entry.get("path"), str
                ):
                    raise ValueError("version entries require a path")
                self.version_dir(entry)
            return data
        except (OSError, ValueError) as exc:
            raise ProjectError(
                "invalid_manifest",
                f"Cannot read {self.manifest_path}: {exc}",
                "Repair the manifest or select an initialized build directory.",
            ) from exc

    def preflight(self) -> None:
        """Probe actual atomic writes; never infer writability from mode bits."""
        try:
            self.build_root.mkdir(parents=True, exist_ok=True)
            state = self.state_dir
            state.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="preflight-", dir=state) as probe:
                source = Path(probe) / "source"
                target = Path(probe) / "target"
                with source.open("wb") as handle:
                    handle.write(b"agentcad\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(source, target)
        except OSError as exc:
            raise ProjectError(
                "build_root_unwritable",
                f"Cannot write build directory {self.build_root}: {exc}",
                "Choose a writable directory with --build-dir or fix its permissions.",
            ) from exc


def resolve_project(
    build_dir: str | None = None, *, start: Path | None = None
) -> ProjectLayout:
    base = (start or Path.cwd()).resolve()
    root = base
    discovered_build = None
    for candidate in (base, *base.parents):
        if (candidate / "agentcad.toml").exists() or (
            candidate / "agentcad.json"
        ).exists():
            root = candidate
            if not (candidate / "agentcad.toml").exists():
                try:
                    manifest = json.loads((candidate / "agentcad.json").read_text())
                    if isinstance(manifest, dict) and isinstance(
                        manifest.get("project_root"), str
                    ):
                        root = Path(manifest["project_root"]).resolve()
                        discovered_build = candidate
                except (OSError, ValueError):
                    pass  # read_manifest supplies the actionable error.
            break
    configured = None
    config_path = root / "agentcad.toml"
    if config_path.exists():
        try:
            data = tomllib.loads(config_path.read_text())
            configured = data.get("build_dir")
            if "build_dir" in data and (
                not isinstance(configured, str) or not configured.strip()
            ):
                raise ValueError("build_dir must be a non-empty string")
        except (OSError, ValueError) as exc:
            raise ProjectError(
                "invalid_project_config",
                f"Cannot read {config_path}: {exc}",
                'Set build_dir to a directory string, for example build_dir = "./build".',
            ) from exc
    if build_dir is not None:
        value, source = build_dir, "command"
    elif configured is not None:
        value, source = configured, "project_config"
    elif discovered_build is not None:
        value, source = str(discovered_build), "manifest"
    else:
        value, source = None, "default"
    try:
        if value is not None and not value.strip():
            raise ValueError("build directory must not be empty")
        build_root = (root / (value if value is not None else ".")).resolve()
        for ancestor in (build_root, *build_root.parents):
            if ancestor.exists() and not ancestor.is_dir():
                raise ValueError(f"{ancestor} is an existing file, not a directory")
    except (OSError, ValueError, RuntimeError) as exc:
        raise ProjectError(
            "invalid_build_root",
            f"Invalid build directory {value!r}: {exc}",
            "Choose a directory with --build-dir, for example --build-dir ./build; "
            "initialize it with agentcad init --build-dir ./build before running.",
        ) from exc
    return ProjectLayout(root, build_root, source)


def get_project() -> ProjectLayout:
    """Cache only within a Click invocation, never across daemon/MCP requests."""
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return resolve_project()
    if "project_layout" not in ctx.meta:
        ctx.meta["project_layout"] = resolve_project()
    return ctx.meta["project_layout"]


def validate_version_label(label: str) -> None:
    if any(character in label for character in ("/", "\\", "\0")):
        raise ProjectError(
            "invalid_version_label",
            "Version labels cannot contain path separators.",
            "Use --label for a simple name (for example --label first) and "
            "--build-dir for the destination. Read outputs.step for the generated STEP path.",
        )


def project_options(function):
    """Share CLI selection across commands, including nested parts callbacks."""

    @click.option(
        "--build-dir",
        default=None,
        metavar="PATH",
        help=(
            "Artifact/history root. Relative to the project root; overrides "
            "agentcad.toml build_dir, then defaults to the project root. See docs artifacts."
        ),
    )
    @wraps(function)
    def wrapped(*args, build_dir=None, **kwargs):
        ctx = click.get_current_context()
        if ctx.command.name == "run":
            ctx.meta["run_label"] = kwargs.get("label") or kwargs.get("legacy_output")
        if build_dir is not None:
            ctx.meta["project_layout"] = resolve_project(build_dir)
        layout = get_project()
        name = ctx.command.name
        initialize = name == "init" or (name == "import" and kwargs.get("init_flag"))
        writes = name in {
            "init",
            "run",
            "import",
            "render",
            "export",
            "view",
            "recover",
            "feedback",
        } or (name == "diff" and kwargs.get("visual"))
        if kwargs.get("dry_run"):
            writes = False
            ctx.meta["project_dry_run"] = True
        if name in {"run", "context", "recover", "parts"} or (
            name == "import" and not initialize
        ):
            if layout.configured:
                layout.read_manifest()
        elif (
            layout.configured
            and layout.manifest_path.exists()
            and name not in {"init", "docs"}
        ):
            layout.read_manifest()
        if writes:
            layout.preflight()
        return function(*args, **kwargs)

    return wrapped


def derived_dir(command: str, *inputs: Path) -> Path:
    """Keep legacy standalone behavior; contain configured derived artifacts."""
    layout = get_project()
    first = inputs[0].resolve()
    if not layout.configured:
        return inputs[0].parent
    if all(path.resolve().is_relative_to(layout.build_root) for path in inputs):
        return layout.artifact_path(first.parent)
    digest = hashlib.sha256(
        "\0".join(str(p.resolve()) for p in inputs).encode()
    ).hexdigest()[:16]
    stem = re.sub(r"[^\w.-]", "_", first.stem)[:64]
    result = layout.artifact_path(f"derived/{command}/{stem}-{digest}")
    try:
        result.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProjectError(
            "build_root_unwritable",
            f"Cannot create derived directory {result}: {exc}",
            "Choose a writable directory with --build-dir or fix its permissions.",
        ) from exc
    return result


def format_response(payload: dict, layout: ProjectLayout) -> dict:
    """Translate stored version-relative paths only in the emitted response.

    Metadata keeps portable build-root-relative paths. Do not reinterpret
    arbitrary strings (labels, metric names, script source) as filenames.
    """
    path_keys = {
        "path",
        "registered_path",
        "step",
        "script",
        "viewer",
        "viewer_glb",
        "preview",
        "png",
        "glb",
        "stl",
        "obj",
        "html",
        "overlay",
        "side_by_side",
        "overlay_png",
        "volume_glb",
        "volume_png",
        "source_copy",
    }

    def visit(value, key="", paths=False):
        if isinstance(value, dict):
            return {
                k: visit(v, k, key in {"outputs", "renders"}) for k, v in value.items()
            }
        if isinstance(value, list):
            return [visit(v, key, paths) for v in value]
        if (
            (layout.configured or Path.cwd().resolve() != layout.project_root)
            and isinstance(value, str)
            and (key in path_keys or paths)
            and re.match(r"^v\d+(?:_|/)", value)
            and "/" in value
        ):
            return layout.response_path(layout.artifact_path(value))
        return value

    result = visit(payload)
    if layout.configured:

        def recovery_commands(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if (
                        key == "recovery_command"
                        and isinstance(item, str)
                        and "--build-dir" not in item
                    ):
                        value[key] = (
                            item + " --build-dir " + shlex.quote(str(layout.build_root))
                        )
                    else:
                        recovery_commands(item)
            elif isinstance(value, list):
                for item in value:
                    recovery_commands(item)

        recovery_commands(result)
        if result.get("scaffold") == "edit.py":
            result["scaffold"] = str(layout.project_root / "edit.py")
    result.update(layout.describe())
    return result
