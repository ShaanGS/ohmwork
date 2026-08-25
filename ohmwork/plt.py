"""LTspice plot-settings (.plt) writer.

Why: "obtain and observe the waveform" asks mean the student should
open the deliverable and see the right traces already plotted — the
observing is theirs to do, which is the pedagogically correct outcome.
LTspice auto-loads a .plt with the same basename as the .asc/.raw.

Format derived from REAL files shipped with LTspice 26.0.2.1:
  %LOCALAPPDATA%/LTspice/examples/Educational/160.plt
    - [Transient Analysis] header matching the raw Plotname
    - Npanes N, then one { } block per pane
    - traces: <count> {<colour id>,<axis>,"<trace or expression>"}
    - X/Y lines: (unit-prefix char, flags, start, tick, stop)
  butter.plt confirms the shape for other analysis types.

The pane colour ids below are copied verbatim from 160.plt (a real
5-pane file), cycled if more panes are needed. They are NOT documented
values; do not edit them except from another real file.

LIMIT: LTspice batch mode does not read .plt, so generated files
cannot be verified headless. Status: derived from real files, pending
one visual confirmation in the GUI (see CLAUDE.md).
"""

# Verbatim from 160.plt, in its pane order.
_COLOUR_IDS = [524293, 268959748, 268959747, 268959746, 524294]


def _axis_line(label: str, start: float, stop: float) -> str:
    tick = (stop - start) / 10 if stop > start else 1
    return f"      {label}: (' ',0,{start:g},{tick:g},{stop:g})"


def _pane(pane: dict, colour_id: int, x_start: float, x_stop: float) -> str:
    margin = 0.05 * (pane["ymax"] - pane["ymin"]) or 0.1
    lines = [
        "   {",
        f'      traces: 1 {{{colour_id},0,"{pane["expr"]}"}}',
        _axis_line("X", x_start, x_stop),
        _axis_line("Y[0]", pane["ymin"] - margin, pane["ymax"] + margin),
        "      Y[1]: ('_',0,1e+308,0,-1e+308)",
        "      Log: 0 0 0",
        "      GridStyle: 1",
        "   }",
    ]
    return "\r\n".join(lines)


def render_plt(
    analysis_name: str, panes: list[dict], x_start: float, x_stop: float
) -> str:
    """One pane per waveform, stacked, like the real 160.plt.

    panes: [{"expr": trace-or-expression, "ymin": .., "ymax": ..}] —
    the y ranges come from measured waveform stats, so the plot opens
    framed on the actual signal instead of autoranged on startup junk.
    """
    body = ",\r\n".join(
        _pane(p, _COLOUR_IDS[i % len(_COLOUR_IDS)], x_start, x_stop)
        for i, p in enumerate(panes)
    )
    return (
        f"[{analysis_name}]\r\n"
        "{\r\n"
        f"   Npanes: {len(panes)}\r\n"
        f"{body}\r\n"
        "}\r\n"
    )


def write_plt(path, analysis_name, panes, x_start, x_stop) -> None:
    with open(path, "w", encoding="ascii", newline="") as f:
        f.write(render_plt(analysis_name, panes, x_start, x_stop))
