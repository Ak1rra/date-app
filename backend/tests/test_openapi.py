from app.openapi_export import OPENAPI_PATH, render_openapi


def test_committed_openapi_is_fresh() -> None:
    committed = OPENAPI_PATH.read_bytes().decode("utf-8")
    assert committed == render_openapi(), (
        "backend/openapi.json is stale; run `uv run python -m app.openapi_export`"
    )
    assert "\r" not in committed
