from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
SERVER_ROOT = REPO_ROOT / "server"


def test_collector_image_has_chrome_default_and_explicit_chromium_target() -> None:
    dockerfile = (SERVER_ROOT / "Dockerfile").read_text()

    assert "FROM runtime-base AS collector-runtime-chromium" in dockerfile
    assert "FROM collector-runtime-chromium AS collector-runtime" in dockerfile
    assert "playwright install --with-deps chromium" in dockerfile
    assert "playwright install --with-deps chrome" in dockerfile
    assert (
        'ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/collector/entrypoint.sh"]'
        in dockerfile
    )


def test_collector_diagnostic_scripts_are_in_the_image_context() -> None:
    dockerignore = (SERVER_ROOT / ".dockerignore").read_text().splitlines()
    assert "scripts" not in dockerignore
    assert (SERVER_ROOT / "scripts/collector/runtime_smoke.py").is_file()
    assert (SERVER_ROOT / "scripts/collector/entrypoint.sh").is_file()


def test_production_compose_defaults_to_chrome_and_checks_startup_marker() -> None:
    compose = (REPO_ROOT / "compose.production.yaml").read_text()
    env_example = (REPO_ROOT / "deploy/production.env.example").read_text()

    assert "FARESCOPE_COLLECTOR_BROWSER_CHANNEL:-chrome" in compose
    assert "FARESCOPE_COLLECTOR_IMAGE_TARGET:-collector-runtime" in compose
    assert "/tmp/farescope-home/browser-ready" in compose
    assert "FARESCOPE_SECRET_ENCRYPTION_KEY" in compose
    assert "FARESCOPE_COLLECTOR_BROWSER_CHANNEL=chrome" in env_example
    assert "FARESCOPE_COLLECTOR_IMAGE_TARGET=collector-runtime" in env_example


def test_collector_entrypoint_has_valid_shell_syntax() -> None:
    script = (SERVER_ROOT / "scripts/collector/entrypoint.sh").read_text()
    assert "browser-smoke" in script
    assert "xvfb-run" in script
    assert "browser-ready" in script
