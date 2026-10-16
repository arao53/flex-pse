# Reverse osmosis model

`ReverseOsmosis` splits a feed stream into permeate (`outlet_a`) and brine
(`outlet_b`). `membrane_model` chooses between two formulations, and
`feed_water` supplies literature defaults for whatever you leave unset.

## Constant recovery (default)

$$
\dot V_{perm}[t] = Y\,\dot V_{feed}[t], \qquad
P_{elec}[t] = e\,\dot V_{perm}[t]
$$

Here $Y$ is the scalar `recovery` and $e$ the `energy_intensity` (kWh per
m³ of permeate, the specific energy consumption the desalination industry
quotes). Both are fixed, regressable Vars, and the model is an LP.
`recovery_min`/`recovery_max` are the bounds of the recovery Var. They bind
once a design mode or a regression unfixes it.

## Linearized membrane

`membrane_model="linearized"` follows Oikonomou and Parvania (2020). The
high-pressure pump, the membrane, the circulation (booster) pump and the
energy recovery device (ERD) are all modeled inside the unit. Recovery
varies by period, so the train can trade water production against power
when prices change. The formulation is an MILP.

### Physics

For permeate $Q_p$, feed $Q_f$ and recovery $Y = Q_p/Q_f$, with membrane area
$S$, water permeability $A$ and feed osmotic pressure $\pi_f$:

$$
\Delta\pi = \frac{\pi_f}{1 - Y}, \qquad
\Delta H = \frac{Q_p}{A\,S} + \Delta\pi
$$

$\Delta\pi$ is the brine-end osmotic pressure, which assumes full salt
rejection. $\Delta H$ (`feed_pressure`) is the transmembrane pressure that
produces the permeate flux. Like the paper, the model uses the brine-end
value rather than a log-mean average, which makes the pressure estimate
conservative.

With an isobaric ERD (pressure exchanger) of efficiency $\eta_E$ and a
membrane pressure drop $\delta p$, the high-pressure pump carries the
permeate-equivalent flow at $\Delta H$. The brine-equivalent flow is
pressurized by the ERD to $\eta_E(\Delta H - \delta p)$, and the
circulation pump makes up the rest:

$$
P_{HP} = \frac{Q_p\,\Delta H}{\eta_p}, \qquad
P_{circ} = \frac{(Q_f - Q_p)\,\big[(1-\eta_E)\,\Delta H + \eta_E\,\delta p\big]}{\eta_p}, \qquad
P_{elec} = P_{HP} + P_{circ}
$$

When $\eta_E = 0$ (no ERD), this reduces to $P_{elec} = Q_f\,\Delta H/\eta_p$:
the high-pressure pump carries the whole feed. One `pump_efficiency`
$\eta_p$ is shared by both pumps. `energy_intensity` is not built in this
mode.

### Piecewise-linear interpolation

Every quantity above is a function of $(Q_p, Q_f)$ alone. The unit samples
those functions on a grid and interpolates them with the convex-combination
triangulation of D'Ambrosio et al. (2010), which the paper uses in its
eqs. 35–41.

- **Grid.** The vertices sit at recovery rays $y_i$
  (`recovery_min`…`recovery_max`) crossed with feed levels $w_j$, at
  coordinates $(y_i w_j,\ w_j)$. Feed spans
  $[\,J_{min}S/Y_{max},\ J_{max}S/Y_{min}\,]$, so the grid covers every
  operating point the flux and recovery windows allow. Each cell is split
  into two triangles (`pwl_triangle_vertices`). Unlike a rectangular
  $(Q_p, Q_f)$ grid, this one never samples $Y \ge 1$, where $\Delta\pi$
  diverges. `breakpoints` (default 5) sets the number of points per axis.
- **Weights.** `pwl_weight[t, i, j]` sums to one when the train runs
  (`pwl_convexity`). It is non-zero only on the vertices of the one
  triangle selected by the binary `pwl_triangle[t, k]` (`pwl_triangle_choice`,
  `pwl_adjacency`). Feed, permeate, `osmotic_pressure`, `feed_pressure`,
  `power_hp_pump` and `power_circulation_pump` are all the same weighted sum
  of their vertex values.
- **Off state.** The binary `membrane_off[t]` sets every weight to zero,
  so feed, permeate, pressure and power are all zero. It also lifts the flux
  floor, so the train can shut down without unit-commitment logic.
  `relax`/`unrelax` switch the triangle and off binaries together with any
  unit-commitment binaries.

The grid geometry is fixed at build time from the configured recovery and
flux windows. The physics parameters (`water_permeability`,
`feed_osmotic_pressure`, `pump_efficiency`, `erd_efficiency`,
`membrane_pressure_drop`) are fixed Vars inside the vertex expressions, so
`update_parameters` and regression change them in place.

## Flux limits

Setting `membrane_area` (m²) builds, in either mode,

$$
J_{min}\,S\,(1 - \text{off}[t]) \le Q_p[t] \le J_{max}\,S,
$$

with $J$ in L/(m² h) (LMH). In constant-recovery mode there is no off
binary, so the floor holds in every period. Leave `membrane_area` unset if
a constant-recovery train must be able to shut down.

## Feed water presets

| Option | Seawater | Brackish | Units |
| --- | --- | --- | --- |
| `recovery` | 0.45 | 0.75 | – |
| `recovery_min` – `recovery_max` | 0.35 – 0.50 | 0.65 – 0.85 | – |
| `flux_min` – `flux_max` | 12 – 19 | 20 – 34 | L/(m² h) |
| `water_permeability` | 1.2 | 3.0 | L/(m² h bar) |
| `feed_osmotic_pressure` | 27 | 2.3 | bar |
| `erd_efficiency` | 0.96 | 0 | – |

- **Recovery.** Seawater trains run at 35–50 % recovery, limited by the
  osmotic pressure of the concentrate. Energy Recovery puts the lowest total
  cost at about 40–45 %, and Lenntech's design table gives 35–45 %. Brackish
  trains run at 65–85 %, limited by scaling: Lenntech gives 65–80 %, and
  70–85 % is common industry practice.
- **Flux.** These are DuPont FilmTec design ranges for the average system
  flux of 8-inch elements (Form 45-D01695, Rev. 14, 2026). Seawater spans
  12–19 LMH across conventional, membrane and UF pretreatment, with the
  open intake / UF column at 15–19. Brackish spans the surface water
  columns (20–34 LMH) and well water (27–34 LMH). The same table caps a
  single element at 32–36 LMH for seawater and 39 LMH for well water. The
  design ranges are typical values, not hard limits, so widen
  `flux_min`/`flux_max` toward those caps to study aggressive operation.
- **Permeability.** Back-calculated from FilmTec element test conditions:
  SW30-class elements give about 1.1–1.4 LMH/bar, and BW30-class elements
  about 3 LMH/bar.
- **Osmotic pressure.** About 0.77 bar per g/L of NaCl-equivalent TDS:
  35 g/L seawater gives 27 bar, and 3 g/L brackish water gives 2.3 bar.
- **ERD.** Isobaric pressure exchangers transfer 94–98 % of the brine
  energy. Brackish trains usually have no ERD.

## References

- K. Oikonomou and M. Parvania, "Optimal participation of water desalination
  plants in electricity demand response and regulation markets," *IEEE
  Systems Journal* 14(3), 3729–3739, 2020.
- C. D'Ambrosio, A. Lodi and S. Martello, "Piecewise linear approximation of
  functions of two variables in MILP models," *Oper. Res. Lett.* 38(1),
  39–46, 2010.
- Lenntech, "Reverse osmosis plants" design table,
  https://www.lenntech.com/systems/reverse-osmosis/ro/rosmosis.htm.
- Energy Recovery, "High recovery seawater reverse osmosis,"
  https://energyrecovery.com/resources/high-recovery-seawater-reverse-osmosis/.
- DuPont, "Membrane System Design Guidelines for 8-inch FilmTec Elements,"
  Form No. 45-D01695-en, Rev. 14, 2026.
