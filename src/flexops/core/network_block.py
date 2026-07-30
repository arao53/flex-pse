"""NetworkBlock: a composition of plant blocks (architecture §3.3, R7).

A portfolio / campus / multi-facility system. Same thin ``dynamic=False``
flowsheet over the ``TimeBlock``'s set as
:class:`~flexops.core.plant_block.PlantBlockData`, but it composes **plants**:
its totals are the sums of its child plants' totals, which are in turn the sums
of *their* units' draws. That is the composition invariant, and the reason the
network never re-walks a plant's units — doing both levels at once would
double-count every unit.

Unlike a plant, a network does not wire state between units with arcs. It links
plants at the *quantity* level instead: :meth:`NetworkBlockData.add_link`
constrains any two time-indexed quantities on any two child plants (heat out vs.
heating load, product out vs. feed in), and product aggregation decides whether
two plants' streams may be mixed at all.
"""

import pyomo.environ as pyo
from idaes.core import declare_process_block_class

from flexcore import nomenclature as nm
from flexops.core.plant_block import (
    _TOTAL_POWER,
    TOTAL_PRODUCT,
    PlantBlockData,
    _AggregatingFlowsheet,
)

EQ_PRODUCT_QUALITY = "eq_product_quality"
"""str: name of the like-quality mixing Constraint, indexed (product, plant, t)."""


@declare_process_block_class("NetworkBlock")
class NetworkBlockData(_AggregatingFlowsheet):
    """A network: a composition of plant blocks (module docstring).

    Example:
        >>> import pyomo.environ as pyo
        >>> import flexops as fo
        >>> from pyomo.environ import units as pyunits
        >>> m = pyo.ConcreteModel()
        >>> m.time_block = fo.TimeBlock(
        ...     start_date="2025-01-01", end_date="2025-01-02",
        ...     time_step=1 * pyunits.hr,
        ... )
        >>> m.campus = fo.NetworkBlock(time_block=m.time_block)
        >>> m.campus.north = fo.PlantBlock(time_block=m.time_block)
    """

    CONFIG = _AggregatingFlowsheet.CONFIG()

    @property
    def plants(self) -> list[PlantBlockData]:
        """list: this network's immediate child plants, in declaration order."""
        return [
            block
            for block in self.component_data_objects(pyo.Block, descend_into=False)
            if isinstance(block, PlantBlockData)
        ]

    def add_link(self, name: str, source, destination) -> None:
        """Constrain two time-indexed quantities across the network to be equal.

        The network-level counterpart of a plant's arcs: it relates quantities
        (a heat duty against a heating load, one plant's product against
        another's feed) rather than copying stream state, so the two sides may
        live on different plants and carry different components.

        Args:
            name: Local name for the Constraint this builds.
            source: A time-indexed quantity (``source[t]``).
            destination: A time-indexed quantity constrained to equal it.
        """
        self.add_component(
            name,
            pyo.Constraint(
                self.time_block.time_index,
                rule=lambda b, t: destination[t] == source[t],
                doc=f"Network link {name}: destination[t] == source[t].",
            ),
        )

    def _power_terms(self, kind: nm.PowerKind) -> list:
        """Return each child plant's power total of ``kind`` (never its units).

        Args:
            kind: The :class:`~flexcore.nomenclature.PowerKind` to collect.

        Returns:
            One term per child plant — the plant's own total.
        """
        return [plant.component(_TOTAL_POWER[kind][0]) for plant in self.plants]

    def _product_terms(self) -> dict[str, list]:
        """Return each product's child-plant totals, plus this network's own.

        Returns:
            ``{product name: [one total per contributing plant]}``.
        """
        terms = super()._product_terms()
        for plant in self.plants:
            total = plant.component(TOTAL_PRODUCT)
            if total is None:
                continue
            for product in plant.products:
                terms.setdefault(product, []).append(
                    lambda t, _total=total, _product=product: _total[_product, t]
                )
        return terms

    def _build_aggregates(self) -> None:
        """Aggregate the child plants' totals, then require like-quality mixing.

        Recurses into the plants first so their totals exist (and are current)
        before this network sums them; re-entrant and idempotent, like the
        plant's own.
        """
        for plant in self.plants:
            plant._build_aggregates()
        super()._build_aggregates()
        self._build_quality_constraints()

    def _build_quality_constraints(self) -> None:
        """Require every contributing plant's product quality to match the first.

        Registering a quality alongside a product declares that the resource is
        only interchangeable at equal quality, so the network permits mixing
        only between like-quality streams (the linear reading; a flow-weighted
        blend would make every mixing point bilinear). Plants that register a
        product without a quality are unconstrained. Built once — the
        Constraint is never rebuilt or deleted (conventions §9).
        """
        if self.component(EQ_PRODUCT_QUALITY) is not None:
            return
        contributors: dict[str, list] = {}
        for plant in self.plants:
            for product, (_flow, quality) in plant.products.items():
                if quality is not None:
                    contributors.setdefault(product, []).append((plant, quality))

        index = [
            (product, plant.local_name)
            for product, entries in contributors.items()
            for plant, _quality in entries[1:]
        ]
        if not index:
            return

        reference = {
            product: entries[0][1] for product, entries in contributors.items()
        }
        by_plant = {
            (product, plant.local_name): quality
            for product, entries in contributors.items()
            for plant, quality in entries
        }
        self.add_component(
            EQ_PRODUCT_QUALITY,
            pyo.Constraint(
                index,
                self.time_block.time_index,
                rule=lambda b, product, plant, t: by_plant[product, plant][t]
                == reference[product][t],
                doc="Like-quality mixing: every plant contributing a product "
                "must deliver it at the same quality as the first contributor.",
            ),
        )
