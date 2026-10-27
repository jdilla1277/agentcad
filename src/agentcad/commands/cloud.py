"""Explicit cloud operations; all network behavior lives in the shared adapter."""

import json

import click

from agentcad.cloud import cloud_call


def _emit(operation, **kwargs):
    result = cloud_call(operation, **kwargs)
    click.echo(json.dumps(result))
    if result.get("status") in {"error", "failed"}:
        raise click.exceptions.Exit(1)


project_arg = click.argument("project")
version_arg = click.argument("version", type=click.IntRange(min=1))
retry_option = click.option("--idempotency-key", help="Reuse this key and identical input after a lost response.")
cwd_option = click.option("--cwd", type=click.Path(file_okay=False), help="Resolve explicit local files relative to this directory.")


@click.group()
def cloud():
    """Use hosted CAD projects with an API URL and issued credentials.

    Set AGENTCAD_API_URL, AGENTCAD_KEY_ID, and AGENTCAD_KEY_SECRET in the
    process environment. See agentcad docs cloud. No local project is needed.
    """


@cloud.group()
def projects():
    """Create, list, or read projects owned by the configured account."""


@projects.command("create")
@click.option("--name", required=True)
@click.option("--runtime", type=click.Choice(["build123d", "cadquery"]), default="build123d", show_default=True)
def create_project(name, runtime):
    """Create a hosted project."""
    _emit("create_project", name=name, runtime=runtime)


@projects.command("list")
def list_projects():
    """List the configured account's projects."""
    _emit("projects")


@projects.command("get")
@project_arg
def get_project(project):
    """Read one hosted project."""
    _emit("project", project=project)


@cloud.command("run")
@project_arg
@click.argument("script")
@click.option("--label", required=True)
@retry_option
@cwd_option
def run(project, script, label, idempotency_key, cwd):
    """Upload one explicit Python CAD script; return a job immediately."""
    _emit("run", project=project, script=script, label=label, idempotency_key=idempotency_key, cwd=cwd)


@cloud.command("import")
@project_arg
@click.argument("file")
@retry_option
@cwd_option
def import_step(project, file, idempotency_key, cwd):
    """Upload one explicit STEP file; return a job immediately."""
    _emit("import_step", project=project, file=file, idempotency_key=idempotency_key, cwd=cwd)


@cloud.command("measure")
@project_arg
@version_arg
def measure(project, version):
    """Read a stored version's measurements."""
    _emit("measure", project=project, version=version)


@cloud.command("inspect")
@project_arg
@version_arg
@click.option("--ids", is_flag=True)
@click.option("--summary", is_flag=True)
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--validate-only", is_flag=True)
@retry_option
def inspect(project, version, ids, summary, limit, validate_only, idempotency_key):
    """Inspect a stored version; return a job immediately."""
    _emit("inspect", project=project, version=version, ids=ids, summary=summary, limit=limit,
          validate_only=validate_only, idempotency_key=idempotency_key)


@cloud.command("check-spec")
@project_arg
@version_arg
@click.argument("spec_file")
@retry_option
@cwd_option
def check_spec(project, version, spec_file, idempotency_key, cwd):
    """Upload an explicit JSON spec for a stored version; return a job."""
    _emit("check_spec", project=project, version=version, spec_file=spec_file, idempotency_key=idempotency_key, cwd=cwd)


@cloud.command("render")
@project_arg
@version_arg
@retry_option
def render(project, version, idempotency_key):
    """Request the fixed isometric PNG of a stored version; return a job."""
    _emit("render", project=project, version=version, idempotency_key=idempotency_key)


@cloud.command("diff")
@project_arg
@click.argument("from_version", type=click.IntRange(min=1))
@click.argument("to_version", type=click.IntRange(min=1))
@retry_option
def diff(project, from_version, to_version, idempotency_key):
    """Compare two stored versions in source order; return a job."""
    _emit("diff", project=project, from_version=from_version, to_version=to_version, idempotency_key=idempotency_key)


@cloud.command("job")
@project_arg
@click.argument("job")
@click.option("--wait", is_flag=True, help="Poll this job for up to 30 seconds.")
@click.option("--wait-seconds", type=click.IntRange(0, 60), default=None, help="Explicit polling window, at most 60 seconds.")
def job(project, job, wait, wait_seconds):
    """Read job status and its completed result or measurement. Never resubmit."""
    _emit("job", project=project, job=job, wait_seconds=wait_seconds if wait_seconds is not None else 30 if wait else 0)


@cloud.command("download")
@project_arg
@click.argument("destination")
@click.option("--job", default=None)
@click.option("--kind", type=click.Choice(["step", "render", "report"]), default="step", show_default=True)
@click.option("--version", type=click.IntRange(min=1), default=None)
@click.option("--path", default=None, help="Returned version-relative output.step path.")
@click.option("--overwrite", is_flag=True)
@cwd_option
def download(project, destination, job, kind, version, path, overwrite, cwd):
    """Save a private artifact to an explicit local destination.

    Use --job and --kind, or --version and --path for a STEP reference.
    Existing files are preserved unless --overwrite is explicit.
    """
    _emit("download", project=project, destination=destination, job=job, kind=kind,
          version=version, path=path, overwrite=overwrite, cwd=cwd)
