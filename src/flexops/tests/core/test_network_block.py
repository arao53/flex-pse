"""NetworkBlock: plant composition, no double-counting, product aggregation (§3.3)."""

import pyomo.environ as pyo
import pytest
from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits
from pyomo.network import Arc
from pyomo.repn import generate_standard_repn

from flexcore import nomenclature as nm
from flexops import NetworkBlock, PlantBlock
from flexops.core.ops_block import OpsBlockData
from flexops.testing import dummy_time_block
from flexops.unit_models import ConstantEnergyIntensityModel

_POWER_KW = {"plant_a": 3.0, "plant_b": 7.0}


@declare_process_block_class("DummyFuelUnit")
class DummyFuelUnitData(OpsBlockData):
    """A minimal unit that registers one named fuel-usage flow."""

    CONFIG = OpsBlockData.CONFIG()
    CONFIG.declare(
        "fuel_name",
        ConfigValue(default="natural_gas", domain=str, description="Fuel name."),
    )

    def build(self) -> None:
        super().build()
        tb = self._find_time_block()
        usage = pyo.Var(
            tb.time_index,
            initialize=0.0,
            units=pyunits.m**3 / pyunits.hr,
            doc="Fuel usage flow.",
        )
        self.add_component(f"{nm.FUEL_USAGE}_{self.config.fuel_name}", usage)
        self.register_fuel_usage(usage, fuel_name=self.config.fuel_name)


# ``declare_process_block_class`` injects the constructible ``DummyFuelUnit``
# wrapper into this module's namespace at runtime; bind the name explicitly so
# static tools (ruff) resolve it.
DummyFuelUnit = globals()["DummyFuelUnit"]


def _network(n: int = 3):
    """Build a two-plant network, each plant holding one energy-consuming unit."""
    m = dummy_time_block(n)
    m.network = NetworkBlock(time_block=m.time_block)
    for name in _POWER_KW:
        m.network.add_component(name, PlantBlock(time_block=m.time_block))
        plant = m.network.find_component(name)
        plant.surrogate = ConstantEnergyIntensityModel(property_package=m.properties)
    # An inter-plant connection between the two plants' unit ports.
    m.network.plant_a_to_b = Arc(
        source=m.network.plant_a.surrogate.outlet,
        destination=m.network.plant_b.surrogate.inlet,
    )
    m.network._build_aggregates()
    for name, kw in _POWER_KW.items():
        plant = m.network.find_component(name)
        for t in m.time_block.time_index:
            plant.surrogate.power_electrical[t].fix(kw)
    return m


@pytest.mark.unit
def test_network_aggregates_over_plants():
    """Network total == sum of plant totals == sum of unit power_electrical."""
    m = _network()
    total = sum(_POWER_KW.values())
    for t in m.time_block.time_index:
        plant_totals = sum(
            pyo.value(m.network.find_component(name).total_electrical_power[t])
            for name in _POWER_KW
        )
        assert pyo.value(m.network.total_electrical_power[t]) == pytest.approx(
            total, rel=1e-6
        )
        assert plant_totals == pytest.approx(total, rel=1e-6)
        assert pyo.value(m.network.total_thermal_power[t]) == pytest.approx(
            0.0, abs=1e-9
        )


@pytest.mark.unit
def test_no_double_count_units():
    """Each unit's power appears exactly once, with coefficient 1, in the total."""
    m = _network()
    unit_power = [
        m.network.find_component(name).surrogate.power_electrical[0]
        for name in _POWER_KW
    ]
    # A fixed Var folds into the linear repn's constant, hiding the very terms
    # this test counts, so read the total with the draws free.
    for var in unit_power:
        var.unfix()
    repn = generate_standard_repn(
        m.network.total_electrical_power[0].expr, compute_values=True
    )
    contributors = [id(v) for v in repn.linear_vars]
    assert sorted(contributors) == sorted(id(v) for v in unit_power)
    assert list(repn.linear_coefs) == [pytest.approx(1.0)] * len(unit_power)


@pytest.mark.unit
def test_network_aggregates_fuel_usage_over_plants():
    """Network total_fuel_usage == sum of each plant's own fuel total."""
    m = _network()
    for name in _POWER_KW:
        plant = m.network.find_component(name)
        plant.gen = DummyFuelUnit(fuel_name="natural_gas")
    m.network._build_aggregates()

    for name in _POWER_KW:
        plant = m.network.find_component(name)
        for t in m.time_block.time_index:
            plant.gen.fuel_usage_natural_gas[t].fix(2.0)

    for t in m.time_block.time_index:
        plant_totals = sum(
            pyo.value(m.network.find_component(name).total_fuel_usage["natural_gas", t])
            for name in _POWER_KW
        )
        assert pyo.value(m.network.total_fuel_usage["natural_gas", t]) == pytest.approx(
            4.0, rel=1e-6
        )
        assert plant_totals == pytest.approx(4.0, rel=1e-6)


@pytest.mark.unit
def test_no_double_count_fuel_usage():
    """Each unit's fuel flow appears exactly once, with coefficient 1, in the total."""
    m = _network()
    for name in _POWER_KW:
        plant = m.network.find_component(name)
        plant.gen = DummyFuelUnit(fuel_name="natural_gas")
    m.network._build_aggregates()

    unit_fuel = [
        m.network.find_component(name).gen.fuel_usage_natural_gas[0]
        for name in _POWER_KW
    ]
    repn = generate_standard_repn(
        m.network.total_fuel_usage["natural_gas", 0].expr, compute_values=True
    )
    contributors = [id(v) for v in repn.linear_vars]
    assert sorted(contributors) == sorted(id(v) for v in unit_fuel)
    assert list(repn.linear_coefs) == [pytest.approx(1.0)] * len(unit_fuel)


@pytest.mark.unit
def test_network_aggregates_registered_products():
    """A product registered on each plant is summed across the network."""
    m = _network()
    for name in _POWER_KW:
        plant = m.network.find_component(name)
        plant.register_product(plant.surrogate.flow_in, name="permeate")
    m.network._build_aggregates()

    for name in _POWER_KW:
        plant = m.network.find_component(name)
        for t in m.time_block.time_index:
            plant.surrogate.flow_in[t].fix(2.0)

    for t in m.time_block.time_index:
        assert pyo.value(m.network.total_product["permeate", t]) == pytest.approx(
            4.0, rel=1e-6
        )


@pytest.mark.unit
def test_network_requires_like_quality_to_mix():
    """Registered product qualities are constrained equal across contributors."""
    m = _network()
    for name in _POWER_KW:
        plant = m.network.find_component(name)
        plant.quality = pyo.Var(
            m.time_block.time_index,
            initialize=100.0,
            units=pyunits.kg / pyunits.m**3,
            doc="Product total dissolved solids.",
        )
        plant.register_product(
            plant.surrogate.flow_in, name="permeate", quality=plant.quality
        )
    m.network._build_aggregates()

    quality_eq = m.network.find_component("eq_product_quality")
    assert quality_eq is not None
    m.network.plant_a.quality[0].fix(100.0)
    m.network.plant_b.quality[0].fix(150.0)
    assert pyo.value(quality_eq["permeate", "plant_b", 0].body) == pytest.approx(
        150.0 - 100.0, rel=1e-6
    )


@pytest.mark.unit
def test_add_link_constrains_two_plant_quantities():
    """add_link builds a network-level equality between two plant quantities."""
    m = _network()
    m.network.add_link(
        "product_to_feed",
        m.network.plant_a.surrogate.flow_out,
        m.network.plant_b.surrogate.flow_in,
    )
    m.network.plant_a.surrogate.flow_out[0].fix(5.0)
    m.network.plant_b.surrogate.flow_in[0].fix(4.0)
    assert pyo.value(m.network.product_to_feed[0].body) == pytest.approx(
        4.0 - 5.0, rel=1e-6
    )
