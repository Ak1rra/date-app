"""Write backend/openapi.json: `python -m app.openapi_export`. The frontend types come from it."""

import json
from pathlib import Path

from app.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def render_openapi() -> str:
    schema = create_app().openapi()
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    OPENAPI_PATH.write_bytes(render_openapi().encode("utf-8"))
    print(f"wrote {OPENAPI_PATH}")


if __name__ == "__main__":
    main()
