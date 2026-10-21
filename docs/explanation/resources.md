# Resource balances

A plant is more than its units. It has a water obligation to meet, a gas supply
to match, a grid connection that can only carry so much. A **resource** is the
name for any one of these. A `resource` element adds a balance for it to a plant
or network, built from what its units already register.

## Where the terms come from

Units tell flex-pse what they consume and produce when they register power,
fuel and boundary flows. A resource balance reads those registrations
({func}`~flexops.core.registration.iter_resource_terms`) and sums them with a
sign. A positive coefficient supplies the resource and a negative one consumes
it.

| Registration | Resource | Coefficient |
| --- | --- | --- |
| `register_boundary_flow(kind=FEED)` | its `resource` | +1 |
| `register_boundary_flow(kind=PRODUCT)` | its `resource` | −1 |
| `register_power(kind=ELECTRICAL)` | `"electricity"` | −1 |
| `register_fuel_usage(var, fuel_name)` | `fuel_name` | −1 |
| {meth}`~flexops.core.ops_block.OpsBlockData.register_resource` | as given | as given |

A `Feed` of `natural_gas` and a unit burning `natural_gas` land on the same
resource with opposite signs, so a balance on `natural_gas` ties the two
together. Thermal power is not mapped, because a heat duty carries a
temperature.

## Two kinds of resource

A **flow** has no memory. At each step the net contribution is the sum of the
terms, and it must lie between `lower` and `upper`. With neither bound it must
be zero, a strict node balance. With only one, the other side is open.

A **stock** accumulates. Its level starts at `initial` and changes by `dt`
times the net contribution at each later step, the same convention as
{class}`~flexops.unit_models.storage.tank.Tank`: `level[0]` is `initial`, and
flows at `t = 0` do not count. The level stays between `lower` and `upper`.

Both are written in `units`, which is a rate for a flow and a quantity for a
stock. Every term is converted to those units, so a mismatch is reported with
the name of the unit that registered the term.

## Two examples

Make the gas a plant draws equal the gas it burns, at every step:

```json
{"kind": "resource", "name": "site.natural_gas", "units": "m^3/hr"}
```

Cap the grid import at 5 MW. A draw is a negative contribution, so the cap is a
lower bound on the net:

```json
{"kind": "resource", "name": "site.grid_cap", "resource": "electricity",
 "units": "kW", "lower": {"value": -5000, "units": "kW"}}
```

Keep a reservoir between 0 and 500 m³, starting at 120 m³:

```json
{"kind": "resource", "name": "site.reservoir", "resource": "water",
 "units": "m^3", "stock": true, "initial": 120,
 "lower": 0, "upper": 500}
```

`name` is the dotted path of the balance block, under a plant or network.
`resource` is the key to collect and defaults to the last part of `name`. In
code, call {func}`~flexops.core.resources.add_resource_balance`.

## Rules to know

- **Arcs already conserve flow.** A term that is a flow on a port an `Arc`
  connects is refused, because balancing it again would count it twice. Register
  the metered quantity instead: a `Feed` or `Product`, a power draw, or a
  variable that is not on a connected port.
- **Do not balance electricity strictly beside a `GridConnection`.** The grid
  unit already ties import and export to the net draw, so a second strict
  balance can over-constrain the model. A bounded electricity resource on a
  plant without a grid unit is the intended use.
- **Nothing is deleted.** The balance adds a block and constraints. It never
  changes a unit.
