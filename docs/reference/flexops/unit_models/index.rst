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

.. currentmodule:: flexops.unit_models.separator

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   Separator

.. currentmodule:: flexops.unit_models.exchanger

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   Exchanger

.. currentmodule:: flexops.unit_models.ro_skid

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   ReverseOsmosisSkid

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
