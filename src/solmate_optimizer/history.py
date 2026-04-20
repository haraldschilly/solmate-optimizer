"""Read-only SolMate history: explore recent logs (PV, injection, battery over time).

The SolMate cloud API exposes a `logs` route (via `solmate_sdk.SolMateAPIClient.get_recent_logs`)
that returns time-series data for the last N days. The exact response shape is not documented;
this command fetches the data and prints a structure summary so callers can decide how to
aggregate it (daily averages, trend lines, etc.).
"""

import datetime
import json
import os
import sys
from typing import Any

import click

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


@click.command()
@click.option("--days", type=int, default=1, help="Number of days of history to fetch (default: 1)")
@click.option("--raw", is_flag=True, help="Dump full JSON response to stdout")
@click.option("--dump", "dump_path", type=click.Path(dir_okay=False, writable=True),
              default=None, help="Write full JSON response to this file")
def history(days: int, raw: bool, dump_path: str | None):
    """Fetch recent logs (PV, injection, battery) from the SolMate cloud and summarize them."""
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
