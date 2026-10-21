"""
Helper para auto-incrementar la versión del modelo NBA.

Lee el mayor número de versión existente en models/ (nba_model_vN.joblib)
y devuelve el siguiente (vN+1). Si no hay modelos, devuelve v1.

Uso:
    python utils/next_model_version.py            # imprime v12
    python utils/next_model_version.py --path X   # directorio custom
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def next_model_version(model_dir: str = "models") -> str:
    """Devuelve la siguiente versión disponible (vN+1) en model_dir."""
    pattern = os.path.join(model_dir, "nba_model_v*.joblib")
    files = glob.glob(pattern)
    max_num = 0
    for f in files:
        name = os.path.basename(f)
        m = re.match(r"nba_model_v(\d+)\.joblib", name)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"v{max_num + 1}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="models", help="Directorio de modelos (default: models)")
    args = parser.parse_args()
    print(next_model_version(args.path))
