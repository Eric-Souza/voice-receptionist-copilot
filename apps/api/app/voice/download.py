"""Download the voice models (about 120 MB) into apps/api/models.

python -m app.voice.download
"""

import urllib.request

from app.config import get_settings
from app.voice import MODEL_FILES


def main() -> None:
    models_dir = get_settings().models_dir
    models_dir.mkdir(parents=True, exist_ok=True)
    for name, url in MODEL_FILES.items():
        target = models_dir / name
        if target.exists():
            print(f"{name}: already there")
            continue
        print(f"{name}: downloading...", flush=True)
        partial = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, partial)
        partial.rename(target)
        print(f"{name}: {target.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
