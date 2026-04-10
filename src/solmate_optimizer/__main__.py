import click

from solmate_optimizer.main import optimize
from solmate_optimizer.status import status


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context):
    if ctx.invoked_subcommand is None:
        ctx.invoke(optimize)


cli.add_command(optimize)
cli.add_command(status)

cli()
