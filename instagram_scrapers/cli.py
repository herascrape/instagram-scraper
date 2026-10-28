from typing import Annotated

import typer

from . import __version__

app = typer.Typer(
    name="instagram-scraper",
    help=(
        "Instagram fake follower checker, giveaway picker, account audit and "
        "follower tracker. Runs on the herascrape API: no Instagram login."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"instagram-scraper {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Instagram scraper CLI."""


def main() -> None:
    app()
