flexops.unit_models
====================

Unit models are organized by inlet/outlet topology first (the ``base``
sub-package), then specialized physically (architecture §3.4, R6). The topology
base owns port construction and the per-stream mass balance; a physical subclass
adds the flow-to-energy relationship and any bounds. ``Pump`` and ``Tank``
subclass the single-inlet/single-outlet ``SISOBlock``; a ``Tank`` additionally
disables the on/off logic layer, since a tank has no unit-commitment status.

Every unit defaults to a **constant energy intensity** and builds it as the
Constraint ``power_electrical_relation`` (``power_thermal_relation`` for a heat
duty). That name is the swap contract: FlexParameterize upgrades a unit's
relationship by deactivating exactly that Constraint and attaching a fitted
replacement, reusing the same registered IO variables — so there is no separate
regression unit class (R11).

**Picking a base when adding a new unit model** (architecture §3.4): ask
whether every port shares one ``property_package``.

- If so, subclass the topology base matching the port count
  (``SISOBlock``/``SIDOBlock``/``DIDOBlock``). It already owns port
  construction and the per-stream mass balance; the new class only renames the
  topology's generic roles into its own nomenclature (``_component_names``)
  and adds the flow-to-energy relationship in ``build()``.
- If the unit needs more than one property package (e.g. a fuel stream and an
  air stream on different flow bases), subclass
  :class:`~flexops.core.ops_block.OpsBlockData` directly instead — declare one
  named property-package config slot per stream family and hand-write the
  ports and balance across them, rather than half-fitting a topology base
  built around a single shared package.

Topology bases
--------------

.. currentmodule:: flexops.unit_models.base.siso

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   SISOBlock

.. currentmodule:: flexops.unit_models.base.sido

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   SIDOBlock

.. currentmodule:: flexops.unit_models.base.dido

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   DIDOBlock

Physical units
--------------

.. currentmodule:: flexops.unit_models.pump

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   Pump

.. currentmodule:: flexops.unit_models.storage_tank

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   Tank

.. currentmodule:: flexops.unit_models.battery

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   BatteryModel

.. currentmodule:: flexops.unit_models.exchanger

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   Exchanger

.. currentmodule:: flexops.unit_models.reverseosmosis

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   ReverseOsmosis

Generic surrogate
-----------------

.. currentmodule:: flexops.unit_models.constant_intensity

The default building block for anything without a bespoke physical topology —
a whole plant modeled as one surrogate, as in the frozen API script.

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   ConstantEnergyIntensityModel
