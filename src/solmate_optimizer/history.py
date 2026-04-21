"""Read-only SolMate history: explore recent logs (PV, injection, battery over time).

Response shape (observed from `SolMateAPIClient.get_recent_logs`):

    {"logs": [ {
        "start": iso, "end": iso, "resolution": int,
        "timestamp":    [iso, ...],          # N points
        "pv_power":     [float, ...],        # total PV, W
        "pv_power_1":   [float, ...],        # per-string (unused here)
        "pv_power_2":   [float, ...],
        "inject_power": [float, ...],        # grid injection, W
        "battery_state":[float, ...],        # SoC (fraction or %)
        "battery_flow": [float, ...],        # charge/discharge, W (unused here)
    }, ... ]}

One bucket per day; arrays inside a bucket are aligned by index. Buckets are
concatenated in response order.
"""

import datetime
import json
import os
import sys
from typing import Any

import click
import plotext as plt

from solmate_optimizer.main import connect_solmate


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
    """Flatten the columnar recent-logs response into aligned per-point lists.

    Returns {"t", "pv", "inject", "battery"} with equal-length lists, or None if
    the response has no usable `logs` bucket.
    """
    if not isinstance(data, dict):
        return None
    buckets = data.get("logs")
    if not isinstance(buckets, list) or not buckets:
        return None

    t: list[datetime.datetime] = []
    pv: list[float | None] = []
    inject: list[float | None] = []
    battery: list[float | None] = []

    def _at(arr: Any, i: int) -> float | None:
        if isinstance(arr, list) and i < len(arr):
            return arr[i]
        return None

    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        timestamps = bucket.get("timestamp") or []
        pv_arr = bucket.get("pv_power")
        inj_arr = bucket.get("inject_power")
        bat_arr = bucket.get("battery_state")
        for i, ts in enumerate(timestamps):
            parsed = _parse_timestamp(ts)
            if parsed is None:
                continue
            t.append(parsed)
            pv.append(_at(pv_arr, i))
            inject.append(_at(inj_arr, i))
            battery.append(_at(bat_arr, i))

    if not t:
        return None
    return {"t": t, "pv": pv, "inject": inject, "battery": battery}


def _drop_nones(xs: list, ys: list) -> tuple[list, list]:
    pairs = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if not pairs:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def plot_history(series: dict[str, list], max_watts: float = 800.0) -> None:
    """Plot PV, injection, and battery with a dual y-axis (watts left, percent right).

    Size: terminal width × max(2/3 of terminal height, 30 lines).
    Battery values 0–1 are treated as fractions and scaled to percent.
    """
    t = series["t"]
    if not t:
        click.echo("No samples to plot.", err=True)
        return

    plt.clf()
    plt.date_form("Y-m-d H:M")
    date_strings = plt.datetimes_to_strings(t, output_form="Y-m-d H:M")
    plt.plotsize(plt.tw(), max(int(plt.th() * 2 / 3), 30))
    plt.title("SolMate history — PV (orange), injection (cyan), battery (green)")

    pv_t, pv_y = _drop_nones(date_strings, series["pv"])
    inj_t, inj_y = _drop_nones(date_strings, series["inject"])
    bat_t, bat_y_raw = _drop_nones(date_strings, series["battery"])

    # Battery may be fraction 0–1 or percent 0–100. Normalize to percent.
    if bat_y_raw and max(bat_y_raw) <= 1.5:
        bat_pct = [v * 100 for v in bat_y_raw]
    else:
        bat_pct = list(bat_y_raw)

    if pv_y:
        plt.plot(pv_t, pv_y, color="orange", label="PV (W)", yside="left")
    if inj_y:
        plt.plot(inj_t, inj_y, color="cyan+", label="injection (W)", yside="left")
    if bat_y_raw:
        plt.plot(bat_t, bat_pct, color="green+", label="battery (%)", yside="right")

    plt.ylim(0, max_watts, yside="left")
    plt.ylim(0, 100, yside="right")
    watt_ticks = [0, max_watts * 0.25, max_watts * 0.5, max_watts * 0.75, max_watts]
    plt.yticks(watt_ticks, [f"{int(w)}" for w in watt_ticks], yside="left")
    plt.yticks([0, 20, 40, 60, 80, 100], ["0", "20", "40", "60", "80", "100"], yside="right")
    plt.ylabel("watts", yside="left")
    plt.ylabel("battery %", yside="right")
    plt.xlabel("time")
    plt.show()


@click.command()
@click.option("--days", type=int, default=7, help="Number of days of history to fetch (default: 7)")
@click.option("--raw", is_flag=True, help="Dump full JSON response to stdout and skip plotting")
@click.option("--dump", "dump_path", type=click.Path(dir_okay=False, writable=True),
              default=None, help="Write full JSON response to this file (plot is still shown)")
@click.option("--no-plot", "no_plot", is_flag=True,
              help="Skip the ASCII plot (print the response structure summary only)")
@click.option("--from-file", "from_file", type=click.Path(exists=True, dir_okay=False, readable=True),
              default=None, help="Load response from a JSON file instead of the cloud (for offline analysis)")
@click.option("--max-watts", type=float, default=800.0, envvar="MAX_WATTS",
              help="SolMate max injection capacity in watts (for plot scaling)")
def history(days: int, raw: bool, dump_path: str | None, no_plot: bool,
            from_file: str | None, max_watts: float):
    """Fetch recent logs (PV, injection, battery) from the SolMate cloud and plot them."""
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

    if no_plot:
        click.echo("\nResponse structure:")
        click.echo(_summarize(data))
        return

    series = extract_series(data)
    if series is None:
        click.echo(
            "\nNo `logs` bucket with usable timestamps in the response. "
            "Run with --raw or --dump FILE and inspect the shape.",
            err=True,
        )
        sys.exit(2)
    click.echo(f"Samples: {len(series['t'])}")
    plot_history(series, max_watts=max_watts)
