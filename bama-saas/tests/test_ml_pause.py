"""`ML_ENABLED` off pauses the learned layer without failing anything else."""

import pytest

from apps.core.models import JobRun
from apps.jobs import pipeline as P
from apps.jobs.health import check_model_staleness


@pytest.fixture
def paused(settings):
    settings.ML_ENABLED = False


@pytest.mark.django_db
def test_paused_ml_steps_are_recorded_as_skipped_without_failing_the_run(paused, monkeypatch):
    ran = []
    monkeypatch.setattr(P, "JOBS", {n: (lambda n=n, **_: ran.append(n) or {}) for n in P.JOBS})

    report = P.run(steps={"deal_scores", "ml_train", "ml_score"})

    assert ran == ["deal_scores"]
    assert report.ok
    assert set(JobRun.objects.filter(status=JobRun.Status.SKIPPED)
               .values_list("name", flat=True)) == {"ml_train", "ml_score"}


@pytest.mark.django_db
def test_paused_ml_is_hidden_from_the_api_and_health(paused, api_client, anonymous_client):
    assert anonymous_client.get("/api/auth/me/").json()["features"] == {"ml": False}
    assert api_client.get("/api/analytics/deal-scores/?band=ml").status_code == 404
    assert check_model_staleness().ok
