"""Read-only SolMate history: explore recent logs (PV, injection, battery over time).

The SolMate cloud API exposes a `logs` route (via `solmate_sdk.SolMateAPIClient.get_recent_logs`)
that returns time-series data for the last N days. The exact response shape is not documented;
this command fetches the data, optionally prints a structure summary, and can plot the three
time series (PV power, injection, battery state) in a single ASCII chart with a shared time axis.
"""

import datetime
import json
import os
import sys
from typing import Any

import click
import plotext as plt

from solmate_optimizer.main import connect_solmate

# Field-name candidates for each metric. The SolMate cloud schema is undocumented,
# so the extractor tries these in order. First match wins.
PV_KEYS = ("pv_power", "pv", "production", "solar", "pv_w")
INJECT_KEYS = ("inject_power", "injection", "inject", "grid_injection", "inject_w")
BATTERY_KEYS = ("battery_state", "battery", "soc", "battery_percentage")
TIMESTAMP_KEYS = ("timestamp", "time", "ts", "date", "datetime")


def _summarize(value: Any, depth: int = 0, max_depth: int = 3) -> str:
    """Return a short human-readable description of a JSON-ish value."""
    indent = "  " * depth
    if isinstance(value, dict):
        if depth >= max_depth:
            return f"dict({len(value)} keys)"
        lines = [f"dict with {len(value)} keys:"]
        for k, v in value.items():
            lines.append(f"{indent}  {k}: {_summarize(v, depth + 1, max_depth)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "list(empty)"
        sample = value[0]
        if isinstance(sample, (dict, list)):
            return f"list(len={len(value)}, item[0]={_summarize(sample, depth + 1, max_depth)})"
        types = {type(v).__name__ for v in value[:50]}
        return f"list(len={len(value)}, item types={sorted(types)})"
    if isinstance(value, str):
        preview = value if len(value) <= 40 else value[:37] + "..."
        return f"str({preview!r})"
    return f"{type(value).__name__}({value!r})"


def _find_sample_list(data: Any) -> list[dict] | None:
    """Walk a nested response and return the first list of dicts that looks like samples.

    Heuristic: a sample dict contains at least one timestamp-like key and at least one
    of the PV/injection/battery keys.
    """
    if isinstance(data, list) and data and isinstance(data[0], dict):
        sample = data[0]
        keys = set(sample.keys())
        has_ts = any(k in keys for k in TIMESTAMP_KEYS)
        has_metric = any(k in keys for k in PV_KEYS + INJECT_KEYS + BATTERY_KEYS)
        if has_ts or has_metric:
            return data
    if isinstance(data, dict):
        for value in data.values():
            found = _find_sample_list(value)
            if found:
                return found
    return None


def _pick(sample: dict, candidates: tuple[str, ...]) -> str | None:
    for key in candidates:
        if key in sample:
            return key
    return None


def _parse_timestamp(value: Any) -> datetime.datetime | None:
    if isinstance(value, (int, float)):
        # Heuristic: > 1e12 → milliseconds, otherwise seconds
        ts = value / 1000 if value > 1e12 else value
        return datetime.datetime.fromtimestamp(ts)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def extract_series(data: Any) -> dict[str, list] | None:
    """Extract (timestamps, pv, injection, battery) from a recent-logs response.

    Returns a dict with keys "t", "pv", "inject", "battery" — each a list of the same length.
    Missing metrics are filled with None; the caller can decide whether to plot them.
    Returns None if no sample list could be found.
    """
    samples = _find_sample_list(data)
    if not samples:
        return None

    ts_key = _pick(samples[0], TIMESTAMP_KEYS)
    pv_key = _pick(samples[0], PV_KEYS)
    inj_key = _pick(samples[0], INJECT_KEYS)
    bat_key = _pick(samples[0], BATTERY_KEYS)

    t: list[datetime.datetime] = []
    pv: list[float | None] = []
    inject: list[float | None] = []
    battery: list[float | None] = []

    for s in samples:
        parsed_ts = _parse_timestamp(s.get(ts_key)) if ts_key else None
        if parsed_ts is None:
            continue
        t.append(parsed_ts)
        pv.append(s.get(pv_key) if pv_key else None)
        inject.append(s.get(inj_key) if inj_key else None)
        battery.append(s.get(bat_key) if bat_key else None)

    return {
        "t": t, "pv": pv, "inject": inject, "battery": battery,
        "keys": {"timestamp": ts_key, "pv": pv_key, "inject": inj_key, "battery": bat_key},
    }


def _drop_nones(xs: list, ys: list) -> tuple[list, list]:
    pairs = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if not pairs:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def plot_history(series: dict[str, list], max_watts: float = 800.0) -> None:
    """Plot PV, injection, and battery on a single time-axis chart.

    PV and injection are plotted in watts (left axis range 0..max_watts). Battery is
    normalized to the same scale as 0..100 % * max_watts / 100, so the battery curve
    sweeps the same vertical space. The y-axis ticks label both views.
    """
    t = series["t"]
    if not t:
        click.echo("No samples to plot.", err=True)
        return

    plt.clf()
    plt.date_form("Y-m-d H:M")
    date_strings = plt.datetimes_to_strings(t, output_form="Y-m-d H:M")
    plt.plotsize(100, 20)
    plt.title("SolMate history: PV (orange), injection (blue), battery (green, %)")

    pv_t, pv_y = _drop_nones(date_strings, series["pv"])
    inj_t, inj_y = _drop_nones(date_strings, series["inject"])
    bat_t, bat_y_raw = _drop_nones(date_strings, series["battery"])

    # Battery may be fraction 0–1 or percent 0–100. Detect and normalize to percent.
    if bat_y_raw and max(bat_y_raw) <= 1.5:
        bat_pct = [v * 100 for v in bat_y_raw]
    else:
        bat_pct = list(bat_y_raw)
    # Scale percent to watts-axis so all three curves share the same vertical range.
    bat_y = [v * max_watts / 100 for v in bat_pct]

    if pv_y:
        plt.plot(pv_t, pv_y, color="orange", label="PV (W)")
    if inj_y:
        plt.plot(inj_t, inj_y, color="blue+", label="injection (W)")
    if bat_y:
        plt.plot(bat_t, bat_y, color="green", label="battery (% scaled)")

    plt.ylim(0, max_watts)
    # Y ticks show watts and equivalent battery percentage side by side.
    watt_ticks = [0, max_watts * 0.25, max_watts * 0.5, max_watts * 0.75, max_watts]
    tick_labels = [f"{int(w)}W / {int(w * 100 / max_watts)}%" for w in watt_ticks]
    plt.yticks(watt_ticks, tick_labels)
    plt.xlabel("time")
    plt.show()


@click.command()
@click.option("--days", type=int, default=1, help="Number of days of history to fetch (default: 1)")
@click.option("--raw", is_flag=True, help="Dump full JSON response to stdout")
@click.option("--dump", "dump_path", type=click.Path(dir_okay=False, writable=True),
              default=None, help="Write full JSON response to this file")
@click.option("--plot", "show_plot", is_flag=True,
              help="Render an ASCII plot with PV, injection and battery over time")
@click.option("--from-file", "from_file", type=click.Path(exists=True, dir_okay=False, readable=True),
              default=None, help="Load response from a JSON file instead of the cloud (for offline analysis)")
@click.option("--max-watts", type=float, default=800.0, envvar="MAX_WATTS",
              help="SolMate max injection capacity in watts (for plot scaling)")
def history(days: int, raw: bool, dump_path: str | None, show_plot: bool,
            from_file: str | None, max_watts: float):
    """Fetch recent logs (PV, injection, battery) from the SolMate cloud and summarize them."""
    if from_file:
        with open(from_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        click.echo(f"Loaded response from {from_file}")
    else:
        serial = os.environ.get("SOLMATE_SERIAL")
        password = os.environ.get("SOLMATE_PASSWORD")
        if not serial or not password:
            click.echo("Error: SOLMATE_SERIAL and SOLMATE_PASSWORD must be set", err=True)
            sys.exit(1)

        try:
            client = connect_solmate(serial, password)
        except Exception as e:
            click.echo(f"SolMate connection failed: {e}", err=True)
            sys.exit(1)

        start = datetime.datetime.now() - datetime.timedelta(days=days)
        click.echo(f"Fetching {days} day(s) of logs since {start.isoformat(timespec='seconds')} ...")

        try:
            data = client.get_recent_logs(days=days)
        except Exception as e:
            click.echo(f"Failed to fetch logs: {e}", err=True)
            sys.exit(1)

    if dump_path:
        with open(dump_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        click.echo(f"Wrote raw response to {dump_path}")

    if raw:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    click.echo("\nResponse structure:")
    click.echo(_summarize(data))

    if show_plot:
        series = extract_series(data)
        if series is None:
            click.echo(
                "\nCould not locate a sample list in the response. "
                "Run with --raw or --dump FILE and inspect the shape, "
                "then extend PV_KEYS / INJECT_KEYS / BATTERY_KEYS in history.py.",
                err=True,
            )
            sys.exit(2)
        mapped = series["keys"]
        click.echo(f"\nMapped keys: {mapped}")
        click.echo(f"Samples: {len(series['t'])}")
        plot_history(series, max_watts=max_watts)
