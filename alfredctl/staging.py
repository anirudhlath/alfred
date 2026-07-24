"""Stage a clean OCI build context from git metadata.

The context contains only files git would track (tracked + untracked-not-ignored),
so gitignored content — .env, secrets/, personal memory files, virtualenvs — can
never enter the image, regardless of ignore-file support in the active runtime.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

CLONE_HINT = (
    "home-service repo not found at {path}.\n"
    "Clone it next to the alfred repo:\n"
    "  git clone https://github.com/anirudhlath/alfred-home-service {path}"
)


def repo_root() -> Path:
    """Root of the current checkout (worktree-aware)."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
    )
    return Path(out.stdout.strip())


def workspace_root() -> Path:
    """Parent directory of the MAIN checkout (where sibling repos live).

    Uses --git-common-dir so invocations from linked worktrees still resolve the
    main repository location.
    """
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(out.stdout.strip()).parent.parent


def _listed_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "-co", "--exclude-standard"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [f for f in out.stdout.split("\0") if f]


def _copy_repo(repo: Path, dest: Path) -> None:
    for rel in _listed_files(repo):
        src = repo / rel
        if not src.is_file():  # ls-files can list deleted-but-tracked paths
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def stage_context(dest: Path) -> Path:
    """Stage alfred/ + home-service/ into *dest* and return it."""
    home_service = workspace_root() / "home-service"
    if not home_service.is_dir():
        raise FileNotFoundError(CLONE_HINT.format(path=home_service))
    if dest.exists():
        shutil.rmtree(dest)
    _copy_repo(repo_root(), dest / "alfred")
    _copy_repo(home_service, dest / "home-service")
    return dest
