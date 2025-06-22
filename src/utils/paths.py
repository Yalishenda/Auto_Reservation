from pathlib import Path

def find_repo_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate project root")

REPO_ROOT   = find_repo_root(Path(__file__).resolve())
CONFIG_DIR  = REPO_ROOT / "config"
SECRETS_PATH = CONFIG_DIR / "secrets.json"
