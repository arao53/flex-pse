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

.. currentmodule:: flexops.unit_models.electrolysis

``ElectrolysisSeparator`` is the one unit with two levels of detail, selected by
its ``detail`` option. The default, ``ElectrolysisDetail.CONSTANT_INTENSITY``,
is the plain separator relationship — one electrical intensity times the feed
flow, no heat duty. ``ElectrolysisDetail.STACK`` instead builds an
**equation-oriented** stack model: a fixed set of variables and residuals with
no assumed causal direction, whose electrochemistry is carried entirely by one
fitted five-coefficient voltage correlation. Fidelity is raised by *estimating
more of those coefficients* — a coarser model is the same equation with
coefficients fitted at zero — rather than by adding equations, so there is no
library of electrochemical constants to source. Because the residuals carry no
direction, the renewables-coupled dispatch case needs no model rewrite: fix
``power_electrical``, unfix ``flow_in``, and the same system solves backward.
Every quantity is a ``Var`` with a defining residual — the unit builds no
``Expression``\ s — so the model is one flat variable/equation set, and only
three of its residuals are nonlinear. ``power_stack`` is the stack's DC draw and
``power_electrical`` the facility's AC draw, separated by the fitted
``rectifier_efficiency``; the rectifier is the **only** balance-of-plant item
modeled, with fluid-side auxiliaries deliberately out of scope. The electrical
draw is the Constraint ``power_electrical_relation`` at either detail level, so
the swap contract holds. ``thermal``
(:class:`ThermalModel`) selects whether the stack has no heat duty, a registered
waste-heat duty, or a steady-state coolant balance that solves its temperature.
An option belonging to a switched-off effect raises ``FlexConfigError`` rather
than being silently ignored.

.. autosummary::
   :toctree: generated
   :template: unit_model.rst
   :nosignatures:

   ElectrolysisSeparator

.. autosummary::
   :toctree: generated
   :nosignatures:

   ElectrolysisDetail
   ThermalModel

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
