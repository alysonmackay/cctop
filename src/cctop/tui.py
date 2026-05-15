from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Input, Static

from .models import (
    ActiveOccupation,
    BrokenSymmetryCoupling,
    Calculation,
    JobType,
    LocalizedOrbitals,
    McscfState,
    NevptResult,
    SaCasscfTransitions,
    Status,
)
from .scan import summarize_status

STATUS_STYLE = {
    Status.DONE: "bold green",
    Status.FAILED: "bold red",
    Status.SUSPICIOUS: "bold yellow",
    Status.RUNNING: "bold cyan",
    Status.UNKNOWN: "dim",
}

STATUS_GLYPH = {
    Status.DONE: "●",
    Status.FAILED: "✗",
    Status.SUSPICIOUS: "⚠",
    Status.RUNNING: "…",
    Status.UNKNOWN: "?",
}

SORT_KEYS = ("file", "status", "energy", "runtime")
FILTER_CYCLE: tuple[Status | None, ...] = (
    None,
    Status.DONE,
    Status.FAILED,
    Status.SUSPICIOUS,
    Status.RUNNING,
    Status.UNKNOWN,
)


def run_tui(calculations: list[Calculation], root: Path | None = None) -> None:
    CctopApp(calculations, root).run()


@dataclass
class Row:
    calc: Calculation
    key: str


def status_pill(status: Status) -> Text:
    return Text(f"{STATUS_GLYPH[status]} {status.value}", style=STATUS_STYLE[status])


def runtime_spark(seconds: int | None, ceiling: int) -> Text:
    blocks = "▁▂▃▄▅▆▇█"
    if seconds is None or seconds <= 0 or ceiling <= 0:
        return Text("─" * 4, style="dim")
    ratio = min(1.0, seconds / ceiling)
    fill = max(1, int(ratio * 4))
    glyph = blocks[min(len(blocks) - 1, int(ratio * (len(blocks) - 1)))]
    bar = glyph * fill + " " * (4 - fill)
    style = "green" if ratio < 0.4 else "yellow" if ratio < 0.8 else "red"
    return Text(bar, style=style)


def fmt_energy(value: float | None) -> Text:
    if value is None:
        return Text("--", style="dim")
    return Text(f"{value:.6f}", style="bright_white")


def fmt_runtime(value: int | None) -> str:
    if value is None:
        return "--"
    h, rem = divmod(value, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_imag(count: int | None) -> Text:
    if count is None or count == 0:
        return Text("--" if count is None else "0", style="dim" if count is None else "green")
    return Text(str(count), style="bold red")


def display_path(calc: Calculation, root: Path | None) -> str:
    if root is None:
        return str(calc.path.name)
    try:
        return str(calc.path.relative_to(root))
    except ValueError:
        return str(calc.path.name)


def details_renderable(calc: Calculation, root: Path | None) -> Group:
    path = display_path(calc, root)

    summary = Table.grid(padding=(0, 2), expand=True)
    summary.add_column(style="dim", no_wrap=True, ratio=1)
    summary.add_column(ratio=2)

    rows: list[tuple[str, Text | str]] = [
        ("Status", status_pill(calc.status)),
        ("Program", Text(calc.program or "--", style="cyan")),
        ("Version", calc.version or _dash()),
        ("Job type", Text(calc.job_type.value, style="bold magenta")),
        ("Method", calc.method or _dash()),
        ("Basis", calc.basis or _dash()),
        ("Charge / Mult", _charge_mult(calc)),
        ("Final energy", _energy_line(calc.final_energy)),
        ("Gibbs energy", _energy_line(calc.gibbs_energy)),
        ("Imag. freqs", fmt_imag(calc.imaginary_frequency_count)),
        ("Lowest freq", _freq_line(calc.lowest_frequency)),
        ("Runtime", Text(fmt_runtime(calc.runtime_seconds), style="bright_white")),
        ("Resources", _resources(calc)),
        ("Termination", calc.termination or _dash()),
        ("Warnings", _warning_count(calc.warning_count)),
    ]
    for label, value in rows:
        summary.add_row(label, value)

    parts: list = [
        Panel(
            summary,
            title=Text(path, style="bold"),
            border_style="cyan",
            padding=(1, 2),
        )
    ]

    if calc.warnings:
        wtable = Table.grid(padding=(0, 1))
        wtable.add_column(style="bold yellow", no_wrap=True)
        wtable.add_column()
        for w in calc.warnings:
            wtable.add_row(w.code, w.message)
        parts.append(Panel(wtable, title="Warning markers", border_style="yellow", padding=(0, 1)))

    if calc.casscf_final_energy is not None or calc.casscf_states:
        parts.append(_mcscf_panel("CASSCF", calc.casscf_states, calc.casscf_final_energy))
    if calc.mrci_states:
        initial = [s for s in calc.mrci_states if s.kind == "reference"]
        final = [s for s in calc.mrci_states if s.kind != "reference"]
        if initial:
            parts.append(_mcscf_panel(
                "MRCI · initial (reference-space CI)",
                initial,
                None,
                subtitle="weights from the reference-space diagonalization before MR-PT selection",
            ))
        if final:
            parts.append(_mcscf_panel("MRCI · final (after MR-PT selection)", final, None))
    if calc.nevpt2_results:
        parts.append(_nevpt_panel(calc.nevpt2_results))
    if calc.localized_orbitals:
        parts.append(_localized_panel(calc.localized_orbitals))
    if calc.active_occupations:
        parts.append(_active_occ_panel(calc.active_occupations))
    if calc.sa_casscf_transitions:
        parts.append(_sa_transitions_panel(calc.sa_casscf_transitions))
    if calc.bs_coupling:
        parts.append(_bs_coupling_panel(calc.bs_coupling))

    return Group(*parts)


def _mcscf_panel(
    title: str,
    states: list[McscfState],
    final_energy: float | None,
    *,
    subtitle: str | None = None,
) -> Panel:
    body = Table.grid(padding=(0, 1))
    body.add_column()
    if subtitle:
        body.add_row(Text(subtitle, style="dim italic"))
        body.add_row(Rule(style="dim"))
    if final_energy is not None:
        body.add_row(Text(f"Final/averaged energy: {final_energy:.8f} Eh", style="bright_white"))
        body.add_row(Rule(style="dim"))
    for i, state in enumerate(states):
        if i > 0:
            body.add_row(Rule(style="dim"))
        body.add_row(Text(_state_label(state), style="bold magenta"))
        for w in state.weights:
            body.add_row(Text(f"  {w.weight:7.4f}  {w.occupation}", style="dim"))
    return Panel(body, title=title, border_style="magenta", padding=(0, 1))


def _localized_panel(loc: LocalizedOrbitals) -> Panel:
    body = Table.grid(padding=(0, 1))
    body.add_column()

    header_bits = Text()
    if loc.active_range is not None:
        a, b = loc.active_range
        header_bits.append("Active range: ", style="dim")
        header_bits.append(f"MO {a}–{b}", style="bold cyan")
        header_bits.append("    ")
    header_bits.append(f"{loc.strongly_local_count} strongly local", style="green")
    header_bits.append("  ·  ", style="dim")
    header_bits.append(f"{loc.bond_count} two-center bond", style="cyan")
    header_bits.append("  ·  ", style="dim")
    header_bits.append(f"{loc.delocalized_count} delocalized", style="yellow")
    body.add_row(header_bits)

    sections: list[tuple[str, list]] = [
        ("Strongly localized", loc.strongly_local),
        ("Bond-like", loc.bonds),
        ("Delocalized", loc.delocalized),
    ]
    first = True
    for label, items in sections:
        if not items:
            continue
        if not first:
            body.add_row(Text(""))
        first = False
        body.add_row(Text(label, style="bold cyan"))
        for orb in items:
            line = Text()
            line.append(f"  MO {orb.mo}: ", style="bold")
            line.append(orb.composition, style="white")
            body.add_row(line)

    return Panel(body, title="Localized orbitals", border_style="cyan", padding=(0, 1))


def _active_occ_panel(occs: list[ActiveOccupation]) -> Panel:
    body = Table.grid(padding=(0, 2))
    body.add_column(style="dim", no_wrap=True)
    body.add_column()
    body.add_column()
    body.add_row(Text("MO", style="bold dim"), Text("Occupation", style="bold dim"), Text("Bar", style="bold dim"))
    for o in occs:
        bar = _occ_bar(o.occupation)
        body.add_row(str(o.mo), Text(f"{o.occupation:.4f}", style="bright_white"), bar)
    return Panel(
        body,
        title="Active-space natural occupations",
        subtitle="(2 = doubly occupied · 0 = empty)",
        border_style="green",
        padding=(0, 1),
    )


def _occ_bar(value: float) -> Text:
    """Render a 0–2 occupation as a 10-cell bar (one cell = 0.2 electrons)."""
    cells = 10
    filled = max(0, min(cells, round(value / 2.0 * cells)))
    bar = "█" * filled + "░" * (cells - filled)
    if value > 1.6:
        style = "green"
    elif value > 0.4:
        style = "yellow"
    else:
        style = "dim"
    return Text(bar, style=style)


def _sa_transitions_panel(t: SaCasscfTransitions) -> Panel:
    header = Text()
    header.append("Lowest root: ", style="dim")
    header.append(f"root {t.lowest_root}, mult {t.lowest_multiplicity}", style="bold cyan")
    header.append(f"   E = {t.lowest_energy_eh:.6f} Eh", style="bright_white")

    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(style="dim", no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_row(
        Text("State", style="bold dim"),
        Text("Root", style="bold dim"),
        Text("Mult", style="bold dim"),
        Text("ΔE (Eh)", style="bold dim"),
        Text("ΔE (cm⁻¹)", style="bold dim"),
    )
    for tr in t.transitions:
        table.add_row(
            str(tr.state_index),
            str(tr.root),
            str(tr.multiplicity),
            Text(f"{tr.de_eh:.6f}", style="bright_white"),
            Text(f"{tr.de_cm:,.1f}", style="bold green"),
        )

    body = Table.grid(padding=(0, 1))
    body.add_column()
    body.add_row(header)
    body.add_row(Rule(style="dim"))
    body.add_row(table)
    return Panel(body, title="SA-CASSCF transition energies", border_style="green", padding=(0, 1))


def _bs_coupling_panel(bs: BrokenSymmetryCoupling) -> Panel:
    body = Table.grid(padding=(0, 1))
    body.add_column()

    # Headline: J(1) Noodleman + coupling type.
    headline = Text()
    if bs.j1_noodleman is not None:
        headline.append("J(1) Noodleman: ", style="dim")
        headline.append(f"{bs.j1_noodleman:+,.2f} cm⁻¹", style="bold green")
    if bs.coupling_type:
        if bs.j1_noodleman is not None:
            headline.append("   ")
        colour = "red" if bs.coupling_type == "antiferromagnetic" else "blue"
        headline.append(bs.coupling_type.upper(), style=f"bold {colour}")
    if headline.plain:
        body.add_row(headline)
        body.add_row(Rule(style="dim"))

    # Spin / energy summary.
    summary = Table.grid(padding=(0, 2), expand=True)
    summary.add_column(style="dim", no_wrap=True, ratio=1)
    summary.add_column(ratio=2)

    def _row(label: str, value: float | None, fmt: str, style: str = "bright_white") -> None:
        if value is None:
            return
        summary.add_row(label, Text(format(value, fmt), style=style))

    _row("S (high-spin)", bs.s_high_spin, ".1f")
    _row("⟨S²⟩ high-spin", bs.s2_high_spin, ".4f")
    _row("⟨S²⟩ broken-sym", bs.s2_broken_sym, ".4f")
    _row("E (high-spin)", bs.energy_high_spin, ".6f")
    _row("E (broken-sym)", bs.energy_broken_sym, ".6f")
    if bs.delta_e_ev is not None and bs.delta_e_cm is not None:
        summary.add_row(
            "E(HS) − E(BS)",
            Text(f"{bs.delta_e_ev:+.4f} eV  ·  {bs.delta_e_cm:+,.2f} cm⁻¹", style="bright_white"),
        )
    body.add_row(summary)

    # Alternative J formulas.
    alt = Table.grid(padding=(0, 2))
    alt.add_column(style="dim", no_wrap=True)
    alt.add_column(no_wrap=True)
    alt.add_column(style="italic dim", no_wrap=True)
    if bs.j2_bencini is not None:
        alt.add_row("J(2)", Text(f"{bs.j2_bencini:+,.2f} cm⁻¹"), "Bencini–Gatteschi")
    if bs.j3_yamaguchi is not None:
        alt.add_row("J(3)", Text(f"{bs.j3_yamaguchi:+,.2f} cm⁻¹"), "Yamaguchi")
    if bs.j2_bencini is not None or bs.j3_yamaguchi is not None:
        body.add_row(Rule(style="dim"))
        body.add_row(Text("Alternative formulas", style="dim"))
        body.add_row(alt)

    return Panel(body, title="Broken-symmetry magnetic coupling", border_style="red", padding=(0, 1))


def _nevpt_panel(results: list[NevptResult]) -> Panel:
    body = Table.grid(padding=(0, 1))
    body.add_column()
    for i, r in enumerate(results):
        if i > 0:
            body.add_row(Rule(style="dim"))
        label = f"Root {r.root}" + (f", mult {r.multiplicity}" if r.multiplicity is not None else "")
        body.add_row(Text(label, style="bold magenta"))
        body.add_row(Text(f"  Reference E0  : {r.reference_energy:.8f} Eh"))
        body.add_row(Text(f"  Correction dE : {r.correction:.8f} Eh"))
        body.add_row(Text(f"  Total         : {r.total_energy:.8f} Eh", style="bright_white"))
    return Panel(body, title="NEVPT2", border_style="magenta", padding=(0, 1))


def _state_label(state: McscfState) -> str:
    parts = [f"Root {state.root}"]
    if state.block is not None:
        parts.append(f"block {state.block}")
    if state.multiplicity is not None:
        parts.append(f"mult {state.multiplicity}")
    label = ", ".join(parts)
    extras = [f"E = {state.energy:.8f} Eh"]
    if state.reference_weight is not None:
        extras.append(f"W(ref) = {state.reference_weight:.4f}")
    return f"{label}: " + ", ".join(extras)


def _dash() -> Text:
    return Text("--", style="dim")


def _charge_mult(calc: Calculation) -> Text:
    if calc.charge is None or calc.multiplicity is None:
        return _dash()
    return Text(f"{calc.charge} / {calc.multiplicity}")


def _energy_line(value: float | None) -> Text:
    if value is None:
        return _dash()
    return Text(f"{value:.8f} Eh", style="bright_white")


def _freq_line(value: float | None) -> Text:
    if value is None:
        return _dash()
    style = "red" if value < 0 else "white"
    return Text(f"{value:.2f} cm⁻¹", style=style)


def _resources(calc: Calculation) -> Text:
    if calc.nprocs is None and calc.maxcore_mb is None:
        return _dash()
    nprocs = str(calc.nprocs) if calc.nprocs is not None else "?"
    mem = f"{calc.maxcore_mb} MB" if calc.maxcore_mb is not None else "? MB"
    return Text(f"{nprocs} proc · {mem}/proc")


def _warning_count(count: int) -> Text:
    if count == 0:
        return Text("0", style="green")
    return Text(str(count), style="bold yellow")


def _summary_renderable(calculations: list[Calculation]) -> Text:
    counts = summarize_status(calculations)
    total = len(calculations)
    text = Text()
    text.append(f"{total} calculations  ", style="bold")
    pieces = [
        (Status.DONE, "done"),
        (Status.FAILED, "failed"),
        (Status.SUSPICIOUS, "suspicious"),
        (Status.RUNNING, "running"),
        (Status.UNKNOWN, "unknown"),
    ]
    for i, (status, label) in enumerate(pieces):
        if i > 0:
            text.append("  ·  ", style="dim")
        text.append(f"{counts[status]} ", style=STATUS_STYLE[status])
        text.append(label, style="dim")
    return text


class CctopApp(App):
    CSS_PATH = "cctop.tcss"
    TITLE = "cctop"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "clear_search", "Clear", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_home", "Top", show=False),
        Binding("G", "cursor_end", "Bottom", show=False),
        Binding("slash", "focus_search", "Search"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("r", "refresh_view", "Refresh"),
    ]

    sort_key: reactive[str] = reactive("file")
    status_filter: reactive[Status | None] = reactive(None)
    search_query: reactive[str] = reactive("")

    def __init__(self, calculations: list[Calculation], root: Path | None) -> None:
        super().__init__()
        self._all = calculations
        self._root = root
        self._rows: list[Row] = []
        max_runtime = max(
            (c.runtime_seconds for c in calculations if c.runtime_seconds is not None),
            default=0,
        )
        self._runtime_ceiling = max(max_runtime, 1)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(_summary_renderable(self._all), id="summary")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Input(placeholder="Search filename… (esc to clear)", id="search")
                yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="right"):
                yield Static(id="details", expand=True)
        yield Static(id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_column("Status", key="status", width=14)
        table.add_column("File", key="file")
        table.add_column("Job", key="job", width=7)
        table.add_column("Method", key="method", width=14)
        table.add_column("Energy (Eh)", key="energy", width=14)
        table.add_column("Imag", key="imag", width=5)
        table.add_column("Runtime", key="runtime", width=10)
        table.add_column("⏱", key="spark", width=5)
        table.add_column("Warn", key="warn", width=5)
        self._populate()
        self.query_one("#search", Input).display = False
        self._refresh_status_bar()
        table.focus()

    # ---- data ----

    def _filtered_sorted(self) -> list[Calculation]:
        items = list(self._all)
        if self.status_filter is not None:
            items = [c for c in items if c.status == self.status_filter]
        q = self.search_query.strip().lower()
        if q:
            items = [c for c in items if q in str(c.path).lower()]
        items.sort(key=self._sort_func(self.sort_key))
        return items

    def _sort_func(self, key: str):
        if key == "status":
            order = {s: i for i, s in enumerate(
                [Status.FAILED, Status.SUSPICIOUS, Status.RUNNING, Status.DONE, Status.UNKNOWN]
            )}
            return lambda c: (order.get(c.status, 99), str(c.path))
        if key == "energy":
            return lambda c: (c.final_energy is None, c.final_energy if c.final_energy is not None else 0.0)
        if key == "runtime":
            return lambda c: (c.runtime_seconds is None, -(c.runtime_seconds or 0))
        return lambda c: str(c.path).lower()

    def _populate(self) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        self._rows = []
        for idx, calc in enumerate(self._filtered_sorted()):
            key = f"row-{idx}"
            table.add_row(
                status_pill(calc.status),
                Text(display_path(calc, self._root), overflow="ellipsis", no_wrap=True),
                Text(calc.job_type.value, style="magenta"),
                Text(calc.method or "--", style="dim" if calc.method is None else ""),
                fmt_energy(calc.final_energy),
                fmt_imag(calc.imaginary_frequency_count),
                Text(fmt_runtime(calc.runtime_seconds)),
                runtime_spark(calc.runtime_seconds, self._runtime_ceiling),
                _warning_count(calc.warning_count),
                key=key,
            )
            self._rows.append(Row(calc=calc, key=key))
        if self._rows:
            table.move_cursor(row=0)
            self._update_details(self._rows[0].calc)
        else:
            self._update_details(None)

    def _update_details(self, calc: Calculation | None) -> None:
        details = self.query_one("#details", Static)
        if calc is None:
            details.update(Text("No calculations match the current filter.", style="dim"))
        else:
            details.update(details_renderable(calc, self._root))

    def _refresh_status_bar(self) -> None:
        text = Text()
        text.append("sort: ", style="dim")
        text.append(self.sort_key, style="bold cyan")
        text.append("   filter: ", style="dim")
        text.append(
            self.status_filter.value if self.status_filter else "all",
            style=STATUS_STYLE.get(self.status_filter, "bold") if self.status_filter else "bold",
        )
        if self.search_query:
            text.append("   search: ", style="dim")
            text.append(f"“{self.search_query}”", style="italic")
        text.append(f"   {len(self._rows)}/{len(self._all)} shown", style="dim")
        self.query_one("#statusbar", Static).update(text)

    # ---- events ----

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        for row in self._rows:
            if row.key == event.row_key.value:
                self._update_details(row.calc)
                return

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.search_query = event.value
            self._populate()
            self._refresh_status_bar()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self.query_one("#table", DataTable).focus()

    # ---- actions ----

    def action_focus_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search", Input)
        if search.has_focus or search.value:
            search.value = ""
            search.display = False
            self.search_query = ""
            self._populate()
            self._refresh_status_bar()
            self.query_one("#table", DataTable).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#table", DataTable).action_cursor_up()

    def action_cursor_home(self) -> None:
        table = self.query_one("#table", DataTable)
        if self._rows:
            table.move_cursor(row=0)

    def action_cursor_end(self) -> None:
        table = self.query_one("#table", DataTable)
        if self._rows:
            table.move_cursor(row=len(self._rows) - 1)

    def action_cycle_sort(self) -> None:
        idx = SORT_KEYS.index(self.sort_key)
        self.sort_key = SORT_KEYS[(idx + 1) % len(SORT_KEYS)]
        self._populate()
        self._refresh_status_bar()

    def action_cycle_filter(self) -> None:
        idx = FILTER_CYCLE.index(self.status_filter)
        self.status_filter = FILTER_CYCLE[(idx + 1) % len(FILTER_CYCLE)]
        self._populate()
        self._refresh_status_bar()

    def action_refresh_view(self) -> None:
        self.query_one("#summary", Static).update(_summary_renderable(self._all))
        self._populate()
        self._refresh_status_bar()
