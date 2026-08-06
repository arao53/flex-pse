"""Exchanger: harness subclass on the DIDOBlock topology base (§3.4)."""

from pyomo.environ import units as pyunits

from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import Exchanger


class TestExchanger(UnitModelTestHarness):
    """Two inlet / two outlet streams exchanging mass, with an electrical draw."""

    expected_dof = 0

    def configure(self):
        m = dummy_time_block(3)
        m.unit = Exchanger(
            property_package=m.properties,
            transfer_fraction=0.2,
            energy_intensity=0.1 * pyunits.kWh / pyunits.m**3,
        )
        return m, m.unit
