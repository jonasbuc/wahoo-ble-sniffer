"""
Tests for launcher scripts and configuration files – verify ports,
paths, and settings are consistent across the project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestStreamlitConfig:
    """Verify .streamlit/config.toml exists at repo root and has required settings."""

    def test_config_exists_at_repo_root(self):
        cfg = REPO_ROOT / ".streamlit" / "config.toml"
        assert cfg.exists(), f".streamlit/config.toml not found at {cfg}"

    def test_xsrf_protection_disabled(self):
        cfg = REPO_ROOT / ".streamlit" / "config.toml"
        content = cfg.read_text()
        assert "enableXsrfProtection = false" in content, (
            "enableXsrfProtection must be false to allow fragment auto-refresh"
        )

    def test_cors_disabled(self):
        cfg = REPO_ROOT / ".streamlit" / "config.toml"
        content = cfg.read_text()
        assert "enableCORS = false" in content


class TestSimulateRidePort:
    """Verify simulate_ride.py connects to the correct ingest port."""

    def test_uses_port_8766(self):
        script = REPO_ROOT / "live_analytics" / "scripts" / "simulate_ride.py"
        content = script.read_text()
        assert "8766" in content, "simulate_ride.py should connect to port 8766"
        assert "8765" not in content, "simulate_ride.py should NOT reference old port 8765"


class TestRunServerScript:
    """Verify run_server.ps1 references correct ports."""

    def test_banner_shows_8766(self):
        script = REPO_ROOT / "live_analytics" / "scripts" / "run_server.ps1"
        content = script.read_text()
        assert "8766" in content, "run_server.ps1 should show port 8766"
        assert "8765" not in content, "run_server.ps1 should NOT reference old port 8765"


class TestRunDashboardScript:
    """Verify run_dashboard.ps1 sets CWD to repo root for config discovery."""

    def test_cwd_set_to_repo_root(self):
        script = REPO_ROOT / "live_analytics" / "scripts" / "run_dashboard.ps1"
        content = script.read_text()
        assert "RepoRoot" in content, (
            "run_dashboard.ps1 must set CWD to repo root so "
            ".streamlit/config.toml is found by Streamlit"
        )


class TestFreshCloneGitkeep:
    """Verify data directories have .gitkeep so they exist after clone."""

    def test_system_check_data_dir(self):
        p = REPO_ROOT / "live_analytics" / "system_check" / "data" / ".gitkeep"
        assert p.exists(), "system_check/data/.gitkeep missing"

    def test_questionnaire_data_dir(self):
        p = REPO_ROOT / "live_analytics" / "questionnaire" / "data" / ".gitkeep"
        assert p.exists(), "questionnaire/data/.gitkeep missing"
