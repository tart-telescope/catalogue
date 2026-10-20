# Releasing

This repository contains **two independently versioned, independently
released packages**. Each has its own `tasks.py` (a [plumbum](https://plumbum.readthedocs.io/)+
[invoke](https://www.pyinvoke.org/) release runner) and its own publish
pipeline. Read the section for the package you are releasing.

| Package | Repo / directory | Publishes to | Trigger |
|---|---|---|---|
| **Python + Rust clients** | `tart-catalogue-client-py/`, `tart-catalog-client-rs/`, `tart-catalogue-core/` (this repo) | PyPI **and** crates.io | `v*` tag in **this** repo |
| **tart2ms** | `tart2ms/` (separate repo: `tart-telescope/tart2ms`) | PyPI | `v*` tag in the `tart2ms` repo |

Both `tasks.py` runners follow the same philosophy: **the pre-release test
gate runs locally first**, then the version bump, changelog stamp, and tag
are written for you, then the runner monitors CI/CD and the public registry
until the release is actually live. A broken release fails locally, before
anything is published.

---

## Releasing the catalogue clients (this repo)

A single `vX.Y.Z` tag here publishes **both** the Python client (to PyPI) and
the Rust crates (to crates.io) at the same time.

### Prerequisites

- A venv with the dev tooling: `uv sync --project tart-catalog-client-py --group dev`
  (installs `invoke`, `plumbum`, `pytest`).
- `cargo` on `PATH` (for the workspace test gate and `Cargo.lock` refresh).
- `gh` authenticated (`gh auth status`) and `git` push access to
  `tart-telescope/catalogue`.
- Network access (the runner polls CI, PyPI, and crates.io).

### What the version numbers mean

There are three version strings. The **git tag tracks the Rust client crate**
(`tart-catalog-client-rs/Cargo.toml`), which is also the headline version in
`CHANGES.md`.

| Component | Manifest | Bumped |
|---|---|---|
| Rust client `tart-catalogue-client` | `tart-catalog-client-rs/Cargo.toml` | **every release** (this is the tagged version) |
| Rust core `tart-catalogue-core` | `tart-catalogue-core/Cargo.toml` | only with `--bump-core` |
| Python client `tart-catalogue-client` | `tart-catalog-client-py/pyproject.toml` | only with `--bump-py` |

The crates.io workflow publishes each crate **only when its version is new on
the index**, so an unchanged core/Python version is simply skipped — that is
why they are opt-in rather than bumped every time. Bump the core when its
code changed; bump the Python client when its code changed.

### Steps

All commands run from the **repository root**.

**1. Check what would be released**

```sh
uv run --project tart-catalog-client-py invoke version
```

This prints the three current versions and what a release would bump/tag.

**2. Make sure `CHANGES.md` is ready**

Edit the top `## Unreleased` section of [`CHANGES.md`](CHANGES.md) so the
release has notes. On release the `## Unreleased` heading is renamed to
`## vX.Y.Z` (the entries beneath it become the release notes). If you forget,
the release still proceeds but warns that it will be published without notes.

**3. Run the full release**

```sh
uv run --project tart-catalog-client-py invoke release
```

This one command:
1. runs the pre-release gate — `cargo test --workspace` (the core's astropy
   parity vectors gate both crates) and `pytest tart-catalog-client-py/tart_client/`;
2. bumps the **Rust client** patch version, stamps `CHANGES.md`, refreshes
   `Cargo.lock`, and commits as `chore: bump version to X.Y.Z`;
3. creates an annotated tag `vX.Y.Z` and pushes the branch **and** the tag —
   the tag push fires both publish workflows;
4. **monitors CI/CD**: waits for `package.yaml` (Python → PyPI) and
   `publish-crate.yml` (Rust → crates.io) to register and go green;
5. confirms the new versions appear on PyPI and crates.io;
6. prints `RELEASE COMPLETE`.

Add flags as needed:

```sh
uv run --project tart-catalog-client-py invoke release --bump-core   # also bump the core crate (patch)
uv run --project tart-catalog-client-py invoke release --bump-py     # also bump the Python client (patch)
uv run --project tart-catalog-client-py invoke release --bump-core --bump-py   # bump all three
```

A **core MAJOR.MINOR** bump also updates the client's `tart-catalogue-core`
dependency pin automatically, so the published client keeps resolving the
freshly published core. (The default patch bumps never need this.)

**4. Verify**

The runner reports success itself, but you can double-check:
- GitHub Actions: <https://github.com/tart-telescope/catalogue/actions>
- PyPI: <https://pypi.org/project/tart-catalogue-client/>
- crates.io: <https://crates.io/crates/tart-catalogue-client>

### Other release options

```sh
# Tag the CURRENT versions as-is, no bump (e.g. to publish an existing bump):
uv run --project tart-catalog-client-py invoke release --no-bump

# Tag exactly a chosen version (implies --no-bump; the tree is not touched):
uv run --project tart-catalog-client-py invoke release --version 0.6.3

# Emergency re-tag without the local test gate (use sparingly):
uv run --project tart-catalog-client-py invoke release --skip-tests
```

The runner is **idempotent**: if the tag already exists on `origin` it is
verified and skipped, never re-pushed. It also refuses to tag a dirty working
tree.

### If the version was already bumped but not published

A release is only *published* when its `vX.Y.Z` tag is on `origin` (the tag is
what fires the publish workflows). If the bump commit is already on `main` but
the tag was never pushed (so PyPI/crates.io never received it), **do not bump
again** — just push the existing tag:

```sh
git push origin vX.Y.Z          # e.g. git push origin v0.6.2
```

or, to let the runner handle monitoring:

```sh
uv run --project tart-catalog-client-py invoke release --no-bump
```

(`--no-bump` sees the version is already set, tags `vX.Y.Z` from the current
commit, and monitors the publish.)

---

## Releasing tart2ms (separate repo)

`tart2ms` is a pure-Python package (no Rust) in its own repository,
`tart-telescope/tart2ms`. Its release runner lives in that repo as
`tasks.py` and publishes **only** the Python package to PyPI.

Commands run from the **tart2ms repo root**, in a venv with `invoke` +
`plumbum` (its `dev` dependency group: `uv sync --group dev`).

**1. Check what would be released**

```sh
uv run invoke version
```

**2. Write the `CHANGES.md` notes**

The `## Unreleased` section becomes the release notes (its heading carries the
section title, e.g. `## Unreleased — Bug fixes` → `## vX.Y.Z — Bug fixes`).

**3. Release**

```sh
uv run invoke release
```

This runs the test suite (`pytest tart2ms/tests/`), bumps the patch version in
`pyproject.toml` and `CITATION.cff`, stamps `CHANGES.md`, commits as
`release: bump to X.Y.Z`, tags `vX.Y.Z`, pushes (the tag fires
`deploy_module.yaml` → PyPI), then **monitors** the publish workflow and waits
for the version to appear on PyPI, and finally creates the GitHub release from
the changelog section.

Options: `--no-bump`, `--version X.Y.Z`, `--skip-tests` (same meanings as
above). Also idempotent and dirty-tree-safe.

---

## Troubleshooting

- **"tree is dirty"** — commit or stash your changes first; the release
  refuses to tag a dirty tree.
- **Tag already on origin** — the runner verifies and skips it (idempotent).
  If you truly need to move a tag (discouraged — never move a published
  tag), delete it from `origin` and GitHub first.
- **Publish workflow failed** — open the run log
  (`gh run view <id> -R <repo> --log-failed`). For the Rust release the most
  common cause is a crate version already on crates.io (the workflow skips
  those on purpose); for PyPI it is usually a missing/rotated token. Fix the
  cause and re-run the workflow from the Actions tab, or re-tag.
- **Version bumped but nothing published** — the tag never reached `origin`.
  See "If the version was already bumped but not published" above.
