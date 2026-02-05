"""Command-line interface for FTL2."""

import click

from ftl2 import __version__


@click.command()
@click.version_option(version=__version__)
def main() -> None:
    """FTL2 - Refactored automation framework."""
    click.echo(f"FTL2 version {__version__}")
    click.echo("Refactored automation framework")


if __name__ == "__main__":
    main()
