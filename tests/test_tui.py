from __future__ import annotations

from pathlib import Path

import pytest

from cctop.scan import load_calculations
from cctop.tui import CctopApp


FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "testing" / "mock_orca_project"


@pytest.mark.asyncio
async def test_app_boots_and_navigates() -> None:
    calcs = load_calculations(FIXTURE_ROOT)
    assert calcs, "expected fixtures to produce calculations"
    app = CctopApp(calcs, root=FIXTURE_ROOT)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j")
        await pilot.press("k")
        await pilot.press("s")  # cycle sort
        assert app.sort_key == "status"
        await pilot.press("f")  # cycle filter
        assert app.status_filter is not None


@pytest.mark.asyncio
async def test_search_filters_rows() -> None:
    calcs = load_calculations(FIXTURE_ROOT)
    app = CctopApp(calcs, root=FIXTURE_ROOT)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for ch in "failed":
            await pilot.press(ch)
        await pilot.pause()
        assert app.search_query == "failed"
        assert all("failed" in str(row.calc.path).lower() for row in app._rows)
