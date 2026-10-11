"""Unit tests verifying cloud hosting compatibility and endpoint host-independence."""

import json
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config.settings import AppSecrets
from backend.app.analyst.tools.comparison import get_comparison_grid
from backend.app.analyst.tools.trust import compute_trust_report


def test_chatbot_config_uses_relative_url() -> None:
    """Ensure chatbot/config.json does not hardcode localhost."""
    config_path = Path("chatbot/config.json")
    assert config_path.exists(), "chatbot/config.json must exist"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["chatbotStore"]["url"] == "/api/chat", (
        f"Expected relative /api/chat, found {data['chatbotStore']['url']}"
    )


def test_chatbot_html_no_localhost_default() -> None:
    """Ensure chatbot/index.html default store state does not hardcode localhost."""
    index_path = Path("chatbot/index.html")
    assert index_path.exists(), "chatbot/index.html must exist"
    content = index_path.read_text(encoding="utf-8")
    # Verify the initial state default in useChatbotStore
    assert 'url: "/api/chat"' in content
    # Verify the self-healing logic exists
    assert "targetUrl === 'http://localhost:8000/api/chat'" in content
    assert "targetUrl = '/api/chat'" in content


def test_comparison_grid_unconfigured_or_unreachable_db() -> None:
    """Verify get_comparison_grid returns structured dict instead of crashing."""
    unconfigured_secrets = AppSecrets(DB_HOST=None, DATABASE_URL=None)
    result = get_comparison_grid(999, secrets=unconfigured_secrets)
    assert isinstance(result, dict)
    assert "error" in result
    assert result["rfx_id"] == 999


def test_trust_report_unconfigured_or_unreachable_db() -> None:
    """Verify compute_trust_report returns structured TrustReportResult instead of crashing."""
    unconfigured_secrets = AppSecrets(DB_HOST=None, DATABASE_URL=None)
    report = compute_trust_report(999, secrets=unconfigured_secrets)
    assert report.rfx_id == 999
    assert report.overall_confidence == "low"
    assert len(report.recommendations) > 0


def test_analyst_endpoints_graceful_handling() -> None:
    """Verify FastAPI analyst endpoints handle missing/unconfigured DB cleanly."""
    client = TestClient(app)
    # Comparison grid endpoint: expected 404 with error message
    resp_comp = client.get("/api/rfx/999/comparison")
    assert resp_comp.status_code == 404
    data_comp = resp_comp.json()
    assert "detail" in data_comp

    # Trust report endpoint: expected 200 with structured low confidence result
    resp_trust = client.get("/api/rfx/999/trust")
    assert resp_trust.status_code == 200
    data_trust = resp_trust.json()
    assert data_trust["rfx_id"] == 999
    assert data_trust["overall_confidence"] == "low"

    # Artifacts endpoint: expected 200 with empty list
    resp_art = client.get("/api/rfx/999/artifacts")
    assert resp_art.status_code == 200
    assert resp_art.json()["count"] == 0


def test_run_server_script_exists_and_executable() -> None:
    """Verify scripts/run_server.sh exists and is executable."""
    script_path = Path("scripts/run_server.sh")
    assert script_path.exists()
    assert script_path.stat().st_mode & 0o111 != 0, "run_server.sh must be executable"
