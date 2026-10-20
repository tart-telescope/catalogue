"""Release orchestration for the TART catalogue clients (Python + Rust).

Following the example of ../casacure and tart2ms' tasks.py: `invoke release`
runs the pre-release gate locally before any tag is pushed to GitHub, then a
single `v*` tag triggers BOTH publish workflows and the task monitors them,
so a broken release becomes a local failure the operator sees first.

The catalogue repo holds two client packages that a single `v*` tag releases
together:
  * tart-catalog-client-py  -> PyPI      (via .github/workflows/package.yaml)
  * tart-catalog-client-rs  -> crates.io (via .github/workflows/publish-crate.yml),
    a workspace of two crates: tart-catalogue-core (pure maths) and
    tart-catalogue-client (the CLI, depends on core).

Usage (from the catalogue repo root, in a venv with invoke + plumbum):

    invoke version                          # show what would be tagged/bumped
    invoke test                             # the pre-release gate only
    invoke release                          # bump client, stamp CHANGES, tag + push
    invoke release --no-bump                # tag the current version as-is
    invoke release --version 0.6.2          # explicit tag (implies --no-bump)
    invoke release --bump-core              # also bump the core crate (patch)
    invoke release --bump-py                # also bump the Python client (patch)
    invoke release --skip-tests             # tag without the local gate

Versioning model: the git tag tracks the *client crate* version
(`tart-catalog-client-rs/Cargo.toml`), which is the headline version in
CHANGES.md. The core crate and the Python client version independently and
are only bumped when asked (--bump-core / --bump-py), because the crates.io
workflow publishes each crate only when its version is new on the index --
bumping one that has not changed would be a no-op publish anyway.

The release chain:
  1. pre-release gate: `cargo test --workspace` (the core's astropy parity
     vectors gate both crates) and `pytest tart_client/` for the Python
     client.
  2. version bump + CHANGES.md stamp (`## Unreleased` -> `## vX.Y.Z`) +
     `Cargo.lock` refresh + release commit `chore: bump version to X.Y.Z`.
  3. annotated tag vX.Y.Z and `git push origin <branch> vX.Y.Z`; the tag
     push triggers BOTH publish workflows.
  4. monitor: wait for the PyPI (package.yaml) and crates.io
     (publish-crate.yml) runs of the tag to go green.

Idempotent: a tag already on origin is verified and skipped, not re-pushed.
Requires: invoke, plumbum, cargo, pytest + the python client's deps (run in
its venv, or `uv run --project tart-catalog-client-py`), git, gh
(authenticated).
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from invoke import task
from plumbum import FG, local

REPO = Path(__file__).parent
REPO_SLUG = "tart-telescope/catalogue"

CRATE_DIR = REPO / "tart-catalog-client-rs"
CORE_DIR = REPO / "tart-catalogue-core"
PYPROJECT = REPO / "tart-catalog-client-py" / "pyproject.toml"
CHANGES = REPO / "CHANGES.md"

# The two tag-triggered publish workflows and the packages they publish.
PYPI_WORKFLOW = "package.yaml"
CRATES_WORKFLOW = "publish-crate.yml"


# ---------------------------------------------------------------------------
# version + changelog helpers
# ---------------------------------------------------------------------------

def _read_crate_version(toml_path: Path) -> str:
    text = toml_path.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "?"


def _read_versions() -> dict[str, str]:
    return {
        "client": _read_crate_version(CRATE_DIR / "Cargo.toml"),
        "core": _read_crate_version(CORE_DIR / "Cargo.toml"),
        "py": _read_crate_version(PYPROJECT),
    }


def _bump_patch(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def _write_crate_version(toml_path: Path, new: str) -> None:
    text = toml_path.read_text()
    text = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{new}"',
                  text, count=1, flags=re.MULTILINE)
    toml_path.write_text(text)


def _bump_core_dependency_pin(client_toml: Path, core_new: str) -> None:
    """Keep the client's core version pin able to match the new core.

    The client depends on core via `version = "MAJOR.MINOR"` (the compatibility
    range cargo publish resolves). A core MAJOR.MINOR change must move that
    pin or the published client would depend on a range that excludes the
    freshly published core. A core PATCH bump leaves the pin valid and it is
    left untouched.
    """
    core_mm = ".".join(core_new.split(".")[:2])
    text = client_toml.read_text()
    text = re.sub(
        r'(tart-catalogue-core\s*=\s*\{[^}]*?version\s*=\s*")[^"]+(")',
        rf"\g<1>{core_mm}\g<2>",
        text,
        count=1,
    )
    client_toml.write_text(text)


def _changelog_section(version: str) -> str | None:
    s = CHANGES.read_text()
    m = re.search(rf"^## v{re.escape(version)}\b.*?(?=^## |\Z)", s,
                  re.MULTILINE | re.DOTALL)
    return m.group(0).strip() if m else None


def _stamp_changelog(new: str) -> bool:
    """Rename the `## Unreleased` heading to `## vX.Y.Z`.

    CHANGES.md is newest-first and uses a single `## Unreleased` holding the
    pending entries; a release turns that heading into `## vX.Y.Z`. If the
    heading is already a version (hand-written or a re-run) it is left alone
    and False is returned. Idempotent.
    """
    s = CHANGES.read_text()
    if re.search(rf"^## v{re.escape(new)}\b", s, re.MULTILINE):
        return False
    if re.search(r"^## Unreleased[ \t]*$", s, re.MULTILINE):
        s = re.sub(r"^## Unreleased[ \t]*$", f"## v{new}", s, count=1,
                   flags=re.MULTILINE)
        CHANGES.write_text(s)
        return True
    # No Unreleased heading: insert one above the first version section.
    first = re.search(r"^## v", s, re.MULTILINE)
    if not first:
        return False
    CHANGES.write_text(s[: first.start()] + f"## v{new}\n\n" + s[first.start():])
    return True


# ---------------------------------------------------------------------------
# git + cargo helpers
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    return local["git"]["-C", str(REPO), *args]()


def _branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").strip()


def _tag_exists(tag: str) -> bool:
    out = _git("ls-remote", "--tags", "origin", f"refs/tags/{tag}")
    return bool(out.strip())


def _refresh_cargo_lock() -> None:
    """Update Cargo.lock's package versions after a Cargo.toml bump."""
    local["cargo"]["update", "--workspace", "--offline"]()
    status = _git("status", "--porcelain", "--", "Cargo.lock").strip()
    if status:
        print("  Cargo.lock refreshed")


# ---------------------------------------------------------------------------
# CI/CD + registry monitoring
# ---------------------------------------------------------------------------

def _gh(*args: str) -> str:
    return local["gh"][*args]()


def _workflow_run_for_tag(workflow: str, tag: str) -> int | None:
    out = _gh("run", "list", "-R", REPO_SLUG, "--workflow", workflow,
              "--limit", "10", "--json", "databaseId,headBranch")
    for r in json.loads(out):
        if r["headBranch"] == tag:
            return r["databaseId"]
    return None


def _wait_for_run(workflow: str, tag: str, timeout_s: int = 1800) -> int:
    """Wait for the tag's run of a workflow to register; return its id."""
    start = time.time()
    while True:
        run_id = _workflow_run_for_tag(workflow, tag)
        if run_id is not None:
            return run_id
        if time.time() - start > 300:
            raise RuntimeError(
                f"no {workflow} run registered for {tag} in 300 s; check "
                f"https://github.com/{REPO_SLUG}/actions")
        print(f"  waiting for the {workflow} run of {tag} to register...")
        time.sleep(20)


def _watch_run(name: str, run_id: int, timeout_s: int = 1800) -> None:
    print(f"  {name} (run {run_id}):")
    start = time.time()
    while True:
        line = _gh("run", "view", str(run_id), "-R", REPO_SLUG,
                   "--json", "status,conclusion",
                   "--jq", '"\\(.status) \\(.conclusion)"').strip()
        status, _, conclusion = line.partition(" ")
        if conclusion == "success":
            print(f"    {name}: {line}")
            return
        if status == "completed":  # failure / cancelled / timed_out / skipped
            raise RuntimeError(
                f"{name} did not succeed ({line}); see "
                f"gh run view {run_id} -R {REPO_SLUG} --log-failed")
        if time.time() - start > timeout_s:
            raise RuntimeError(f"{name} timed out after {timeout_s}s")
        print(f"    {name}: {line}")
        time.sleep(30)


def _wait_for_pypi(version: str, timeout_s: int = 900) -> None:
    url = f"https://pypi.org/pypi/tart-catalogue-client/{version}/json"
    _wait_for_index(url, f"PyPI tart-catalogue-client {version}", timeout_s)


def _wait_for_crate(version: str, crate: str, timeout_s: int = 900) -> None:
    # The crates.io sparse index layout is <first 2>/<next 2>/<name>.
    idx = f"{crate[:2]}/{crate[2:4]}/{crate}"
    url = f"https://index.crates.io/{idx}"
    _wait_for_index(url, f"crates.io {crate} {version}", timeout_s,
                    present=lambda body: f'"vers":"{version}"' in body)


def _wait_for_index(url: str, label: str, timeout_s: int,
                    present=None) -> None:
    start = time.time()
    while True:
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                body = resp.read().decode()
            ok = present(body) if present else True
            if ok:
                print(f"  {label} is live")
                return
        except Exception:
            pass
        if time.time() - start > timeout_s:
            raise RuntimeError(f"{label} not live after {timeout_s}s")
        print(f"  waiting for {label}...")
        time.sleep(20)


# ---------------------------------------------------------------------------
# pre-release tests
# ---------------------------------------------------------------------------

def _run_tests() -> None:
    print("=== cargo test --workspace (astropy parity vectors gate both crates)")
    local["cargo"]["test", "--workspace"] & FG
    print("=== pytest tart_client/ (Python client)")
    pytest = local[sys.executable]["-m", "pytest"]
    pytest["tart-catalog-client-py/tart_client/", "-q"] & FG


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

@task
def version(c) -> None:
    """Show the current versions and what a release would tag/bump."""
    v = _read_versions()
    print(f"catalogue clients:")
    print(f"  rust client  (tart-catalogue-client)  {v['client']}  "
          f"-> tag tracks this; --no-bump to tag as-is")
    print(f"  rust core    (tart-catalogue-core)    {v['core']}   "
          f"(bump only with --bump-core)")
    print(f"  python client (tart-catalogue-client-py) {v['py']}  "
          f"(bump only with --bump-py)")
    print(f"  CHANGES.md '## Unreleased' -> '## v{_bump_patch(v['client'])}' "
          f"on a default release")


@task
def test(c) -> None:
    """Run the pre-release gate (cargo workspace tests + Python client tests)."""
    _run_tests()
    print("=== test gate PASS")


@task
def release(c,
            version: str | None = None,
            bump: bool = True,
            bump_core: bool = False,
            bump_py: bool = False,
            skip_tests: bool = False) -> None:
    """Run the full catalogue release chain (Python + Rust together).

    1. run the pre-release gate (cargo workspace + Python client tests);
       skip with --skip-tests for an emergency re-tag only.
    2. bump versions (client by default; core/py only with --bump-core /
       --bump-py), stamp CHANGES.md, refresh Cargo.lock, and commit
       `chore: bump version to X.Y.Z`. --no-bump skips the bump; --version
       X.Y.Z tags exactly that (implies --no-bump).
    3. annotated tag vX.Y.Z and push the branch + tag; the tag fires BOTH
       package.yaml (PyPI) and publish-crate.yml (crates.io).
    4. monitor: wait for both workflow runs of the tag to go green, then
       confirm the Python client is on PyPI and the changed crates are on
       crates.io.

    Idempotent: a tag already on origin is verified and skipped, not re-pushed.
    """
    if skip_tests:
        print("=== --skip-tests: the local test gate is NOT run")
    else:
        _run_tests()

    if version is not None:
        bump = False

    v = _read_versions()
    if bump:
        new_client = _bump_patch(v["client"])
        print(f"=== bumping rust client to {new_client}")
        _write_crate_version(CRATE_DIR / "Cargo.toml", new_client)

        new_core = v["core"]
        if bump_core:
            new_core = _bump_patch(v["core"])
            print(f"=== bumping rust core to {new_core}")
            _write_crate_version(CORE_DIR / "Cargo.toml", new_core)
            _bump_core_dependency_pin(CRATE_DIR / "Cargo.toml", new_core)

        new_py = v["py"]
        if bump_py:
            new_py = _bump_patch(v["py"])
            print(f"=== bumping python client to {new_py}")
            _write_crate_version(PYPROJECT, new_py)

        if _stamp_changelog(new_client):
            print(f"=== CHANGES.md: ## Unreleased -> ## v{new_client}")
        _refresh_cargo_lock()
        _git("add", "-A")
        _git("commit", "-m", f"chore: bump version to {new_client}")
        v = _read_versions()

    tag_version = version or v["client"]
    tag = f"v{tag_version}"
    section = _changelog_section(tag_version)
    if not section:
        print(f"    warning: CHANGES.md has no '## v{tag_version}' section, so "
              f"the tag will be published without release notes")

    commit = _git("rev-parse", "HEAD").strip()
    if _tag_exists(tag):
        print(f"=== {tag} already on origin -- verified, skipping")
        return

    status = _git("status", "--porcelain").strip()
    if status:
        raise RuntimeError(
            "tree is dirty; commit the release (CHANGES.md, Cargo.toml, "
            f"Cargo.lock, pyproject.toml) first\n{status}")

    branch = _branch()
    print(f"=== tagging {tag} (commit {commit[:12]})")
    _git("tag", "-a", tag, "-m", tag)
    local["git"]["-C", str(REPO), "push", "origin", branch, tag] & FG

    print("=== monitoring CI/CD (both publish workflows)")
    py_run = _wait_for_run(PYPI_WORKFLOW, tag)
    crates_run = _wait_for_run(CRATES_WORKFLOW, tag)
    _watch_run(PYPI_WORKFLOW, py_run)
    _watch_run(CRATES_WORKFLOW, crates_run)

    print("=== confirming registries")
    _wait_for_pypi(v["py"])
    _wait_for_crate(v["client"], "tart-catalogue-client")
    if bump_core:
        _wait_for_crate(v["core"], "tart-catalogue-core")

    print(f"\n=== RELEASE COMPLETE: catalogue {tag} (python {v['py']} -> PyPI, "
          f"rust client {v['client']} -> crates.io)")
