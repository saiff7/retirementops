"""Fixtures for the M4 evaluation tests: the run-and-score side, built without privileged secrets."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from retirementops.config import Settings
from retirementops.evaluations import dataset as gd
from retirementops.evaluations import runner as R
from retirementops.evaluations.observe import Observer, reader_engine
from retirementops.services.container import Container


@pytest.fixture(scope="session")
def golden() -> gd.GoldenDataset:
    return gd.load()


@pytest.fixture(scope="session")
def unprivileged() -> Settings:
    return Settings.unprivileged()


@pytest.fixture(scope="session")
def eval_container(unprivileged: Settings, eval_db: str) -> Iterator[Container]:
    c = R.build_eval_container(unprivileged)
    yield c
    c.dispose()


@pytest.fixture(scope="session")
def observer(unprivileged: Settings, eval_db: str, golden: gd.GoldenDataset) -> Iterator[Observer]:
    engine = reader_engine(unprivileged)
    yield Observer(engine, golden.manifest.tenants)
    engine.dispose()


@pytest.fixture(scope="session")
def case_runner(eval_container: Container, observer: Observer) -> R.CaseRunner:
    return R.CaseRunner(eval_container, observer)
