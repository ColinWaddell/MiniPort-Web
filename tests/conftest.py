"""Shared fixtures for the eb200 test suite."""

import pytest

from eb200.device import EB200
from eb200.sim import SimulatedTransport


@pytest.fixture
def sim() -> SimulatedTransport:
    return SimulatedTransport.with_defaults()


@pytest.fixture
def device(sim: SimulatedTransport) -> EB200:
    return EB200(sim, registry_settle=0.0)
