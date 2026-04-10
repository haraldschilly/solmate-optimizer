"""Read-only SolMate status: live values and injection profiles."""

import datetime
import os
import sys

import click

from solmate_optimizer.main import connect_solmate
from solmate_optimizer.logic import MAX_WATTS
from solmate_optimizer.plot import plot_profile


@click.command()
@click.option("--graph", is_flag=True, help="Show ASCII art graph for each profile")
def status(graph: bool):
    """Show current SolMate live values and injection profiles (read-only)."""
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

    now = datetime.datetime.now()
    current_hour = now.hour
    click.echo(f"\nSolMate Status — {now.strftime('%Y-%m-%d %H:%M')}")
    click.echo("=" * 40)

    # --- Live values ---
    try:
        live = client.get_live_values()
        pv = live.get("pv_power")
        inject = live.get("inject_power")
        battery = live.get("battery_state")
        if pv is not None:
            click.echo(f"PV power:    {pv:.0f} W")
        if inject is not None:
            click.echo(f"Injection:   {inject:.0f} W")
        if battery is not None:
            click.echo(f"Battery:     {battery*100:.0f}%")
        known = {"pv_power", "inject_power", "battery_state"}
        for k, v in live.items():
            if k not in known:
                click.echo(f"{k}: {v}")
    except Exception as e:
        click.echo(f"Failed to read live values: {e}", err=True)

    click.echo()

    # --- Injection profiles ---
    try:
        settings = client.get_injection_profiles()
        profiles = settings.get("injection_profiles", {})
        profile_name = os.environ.get("SOLMATE_PROFILE_NAME", "dynamic")

        if not profiles:
            click.echo("No injection profiles found.")
        else:
            click.echo(f"Injection profiles ({len(profiles)}):")
            for name in sorted(profiles.keys()):
                marker = "*" if name == profile_name else " "
                cur = profiles[name]
                avg_min = sum(v * MAX_WATTS for v in cur["min"]) / 24
                avg_max = sum(v * MAX_WATTS for v in cur["max"]) / 24
                click.echo(f"  {marker} {name:<20}  avg {avg_min:.0f}–{avg_max:.0f} W")

            if graph and profile_name in profiles:
                cur = profiles[profile_name]
                plot_profile(profile_name, cur["min"], cur["max"], current_hour)

    except Exception as e:
        click.echo(f"Failed to read profiles: {e}", err=True)
        sys.exit(1)
