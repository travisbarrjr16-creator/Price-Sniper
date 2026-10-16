"""Price Sniper: watch marketplaces for an item and surface the cheapest real options."""
import os
import re
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            if v[:1] in ('"', "'") and v[0] in v[1:]:
                v = v[1:v.index(v[0], 1)]                 # "quoted value"  # comment
            else:
                v = re.split(r"(?:^|\s+)#", v, maxsplit=1)[0].strip()   # value   # comment after it
            os.environ.setdefault(k.strip(), v)


if os.getenv("SNIPER_NO_DOTENV") != "1":     # tests set this so your real keys are never used
    _load_dotenv(Path.cwd() / ".env")
