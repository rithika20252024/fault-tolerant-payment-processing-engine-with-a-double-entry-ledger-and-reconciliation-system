import pytest

from payments.runtime import validate_cloud_environment


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "test-project")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/database")
    monkeypatch.setenv("API_TOKEN", "x"*40)
    monkeypatch.setenv("PROVIDER", "stripe")
    monkeypatch.setenv("ENABLE_FAULTS", "false")
    return monkeypatch


def test_cloud_configuration_accepts_private_token(cloud):
    validate_cloud_environment()


@pytest.mark.parametrize("token", ["", "short", "local-demo-token-change-before-sharing"])
def test_cloud_rejects_missing_or_default_token(cloud, token):
    cloud.setenv("API_TOKEN", token)
    with pytest.raises(ValueError, match="API_TOKEN"):
        validate_cloud_environment()


def test_cloud_requires_database(cloud):
    cloud.delenv("DATABASE_URL")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        validate_cloud_environment()


def test_cloud_disables_fault_injection(cloud):
    cloud.setenv("ENABLE_FAULTS", "true")
    with pytest.raises(ValueError, match="Fault injection"):
        validate_cloud_environment()
