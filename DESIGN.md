# Design Notes

This document explains the reasoning behind the optimizer's decisions — not just *what* it does, but *why*. No programming knowledge needed.

## Core idea: let the electricity price do the thinking

The optimizer is **price-driven**. This is a deliberate choice: electricity spot prices on the aWATTar market already reflect supply and demand in real time. When the sun is shining across Europe and wind turbines are running at full capacity, prices drop — sometimes to zero or below. When people come home in the evening and factories are running, prices rise. The price signal therefore implicitly encodes weather, season, and time of day.

This means the optimizer does not need to know *why* prices are high or low — the price itself is the signal.

## Relative prices, not absolute

Rather than hard-coding thresholds like "inject if price > 15 ct/kWh", the optimizer compares each hour's price to the **25th and 75th percentile** (P25 / P75) of all available prices for the day. This makes the logic adaptive:

- **Below P25**: the cheapest quarter of the day — save battery for when it matters.
- **Between P25 and P75**: middle of the range — coast along based on time of day.
- **Above P75**: the most expensive quarter — inject as much as makes sense.

This self-calibrates automatically across seasons, years, and markets without ever needing to update a threshold manually.

## Negative prices: never inject

When prices go negative, the grid operator is paying consumers to *take* electricity away — there is a surplus (usually from wind or solar). Injecting from a home battery into an already-oversupplied grid makes no economic or technical sense. Rule 1 always wins.

## Battery thresholds: protect first, optimize second

The battery can be damaged by repeatedly running it to empty. Two thresholds guard against this:

- **Low threshold (default 25%)**: below this, only a trickle is allowed regardless of price.
- **High threshold (default 75%)**: used during evening hours to decide between *medium* and *high* injection (see below).

The price rules (priorities 1 and 2) sit above battery protection in the priority order. When the battery is low, the optimizer normally allows a small trickle injection rather than cutting to zero — on the theory that covering some household load is better than nothing. But if prices are negative or below P25, that trickle is cut to zero as well. There is no point sending stored power to the grid when electricity is cheap or the grid is already oversupplied.

## Night: no solar, no aggressive injection

From roughly 23:00 to 08:00, there is no sun. Injecting aggressively overnight would drain the battery before it can recharge. The optimizer drops to a minimal baseload level — enough to cover the fridge and standby power, nothing more. This window is configurable (`NIGHTTIME`).

## "Sun expected": deciding between cautious and bold

When a high-price hour arrives during the day, the optimizer must decide: inject at full power (**high**), or hold back (**medium**)? The difference is whether the battery is likely to recharge during the coming day.

The answer comes from the OpenWeatherMap cloud-cover forecast. The heuristic:

> Average the forecast cloud cover for daytime hours (08:00–17:59). If the average is below 60 %, sun is expected.

**Why 60%?** It is a pragmatic threshold — partly cloudy still produces meaningful solar output. The value is configurable (`CLOUD_SUN_THRESHOLD`).

**Which hours?** The range 08–17 represents typical solar production hours in Central Europe. These boundaries happen to coincide with the configurable `nighttime_end` and `evening_start` defaults, but the sun-expected check uses its own fixed window because solar geometry does not depend on household schedules.

**Does it look into the future?** Yes — implicitly. The optimizer always works with the *next upcoming occurrence* of each hour. If it is currently 12:00, then:
- Hours 8–12 in the forecast already point to **tomorrow's** 8–12 (because those hours have already passed today).
- Hours 13–17 still point to **today's** afternoon.

The resulting average is therefore a blend of today's remaining afternoon and tomorrow's morning — a practical approximation of "will there be useful solar production in the next solar window?" It is not a clean split between today and tomorrow, but it is good enough for an hourly decision cycle.

**Why a single boolean instead of per-hour solar?** Simplicity. A full per-hour solar forecast would be more accurate but harder to tune and reason about. The question the optimizer actually needs to answer is: "will the battery recover?" — and that is a question about the *day*, not about individual hours.

## Evening: the transition zone

The period from (default) 18:00 to 22:59 sits between daytime and night. A few things are different in the evening:

- **No more solar charging** — whatever is in the battery is what you have.
- **Household consumption peaks** — cooking, TV, lights are all on.
- **Prices are often high** — demand peaks in the evening too.

The optimizer therefore uses a dedicated "evening" level for middle prices. For high prices during the evening, it distinguishes two battery bands:

- **Battery ≥ 75% (high threshold)**: inject at the full *high* level.
- **Battery 25–75%**: use the *medium* level — the price is worth something, but not worth draining the battery completely before morning.

This avoids waking up to a flat battery on a cloudy day.

## The 24-hour profile as the unit of control

The SolMate does not support real-time injection commands. Instead, it uses *named profiles* — each profile contains two 24-element arrays (minimum and maximum injection per hour). The optimizer computes a fresh profile every hour and writes it only if something changed. Your existing named profiles ("Sonnig", "Schlechtwetter", etc.) are never touched.

## What the optimizer does not do

- It does not account for shading, panel orientation, or local solar irradiance — only cloud cover as a proxy.
- It does not model the battery's charge/discharge curve or degradation.
- It does not forecast tomorrow's prices (aWATTar typically publishes the next day's prices in the early afternoon).
- It does not coordinate with other household loads (washing machine, EV charging, etc.).

These are known limitations accepted in exchange for simplicity and low operational cost.
