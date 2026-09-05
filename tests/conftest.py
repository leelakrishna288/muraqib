import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Hermetic defaults: no network, no API keys, no downloads, no cost.
os.environ.setdefault("MURAQIB_PROVIDER", "offline")
os.environ.setdefault("MURAQIB_EMBEDDING_BACKEND", "hashing")
os.environ.setdefault("MURAQIB_VECTOR_BACKEND", "memory")
os.environ.setdefault("MURAQIB_LOG_LEVEL", "WARNING")

import pytest  # noqa: E402

from muraqib.config import get_settings  # noqa: E402
from muraqib.corpus import Corpus  # noqa: E402
from muraqib.models import DataCategory, Endpoint, ModelSpec, PlatformConfig  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MURAQIB_DATA_DIR", str(tmp_path / "data"))
    get_settings(refresh=True)
    yield
    get_settings(refresh=True)


@pytest.fixture
def settings():
    return get_settings(refresh=True)


@pytest.fixture(scope="session")
def corpus():
    return Corpus.load()


@pytest.fixture
def platform():
    return PlatformConfig(
        platform_name="Tenant Service Assistant",
        owner_org="Example Group",
        jurisdiction=["UAE", "Saudi Arabia"],
        purpose="Answer tenant queries and triage maintenance requests",
        deployment="private_cloud",
        data_categories=[DataCategory.PERSONAL, DataCategory.FINANCIAL],
        data_residency="UAE",
        cross_border_transfer=True,
        affects_individuals=True,
        automated_decision_making=True,
        human_in_the_loop=False,
        endpoints=[
            Endpoint(name="chat", authenticated=True, auth_scheme="OIDC", logs_requests=True)
        ],
        models=[ModelSpec(name="gpt-4o-mini", provider="openai", hosting="saas", region="us-east")],
        controls_documented={
            "NDMO.DG.01": "Governance charter documented and approved; committee meets monthly, minutes retained.",
            "NDMO.CL.03": "Not implemented. Embeddings carry no classification labels.",
            "NDMO.SP.01": "Entra ID SSO enforced with MFA; RBAC matrix reviewed quarterly and access reviews logged.",
        },
    )
