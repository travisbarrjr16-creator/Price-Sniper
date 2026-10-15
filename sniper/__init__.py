"""Price Sniper: watch marketplaces for an item and surface the cheapest real options."""
import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if os.getenv("SNIPER_NO_DOTENV") != "1":     # tests set this so your real keys are never used
    _load_dotenv(Path.cwd() / ".env")
