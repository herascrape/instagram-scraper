from typer.testing import CliRunner

from instagram_scrapers import __version__
from instagram_scrapers.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"instagram-scraper {__version__}"
