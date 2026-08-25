"""Tests for ohmwork.plt: the plot-settings file for "observe the
waveform" asks.

Format derived from real files shipped inside LTspice 26.0.2.1
(examples/Educational/160.plt — a 5-pane transient, exactly the Q3
shape — and butter.plt). The pane colour ids are copied verbatim from
160.plt, not invented.

LIMIT: batch mode does not read .plt, so a generated file cannot be
verified headless. Status is derived-from-real-file, pending one visual
confirmation in LTspice; recorded in CLAUDE.md.
"""

from ohmwork.plt import render_plt

PANES = [
    {"expr": "V(ac1)-V(ac2)", "ymin": -17.0, "ymax": 17.0},
    {"expr": "V(vrect)", "ymin": 14.7, "ymax": 15.8},
    {"expr": "V(vout)", "ymin": 6.24, "ymax": 6.26},
]


def rendered():
    return render_plt("Transient Analysis", PANES, x_start=0.1, x_stop=0.2)


def test_header_names_the_analysis():
    assert rendered().startswith("[Transient Analysis]\r\n")


def test_pane_count_and_traces():
    text = rendered()
    assert "Npanes: 3" in text
    for pane in PANES:
        assert f'"{pane["expr"]}"' in text


def test_x_axis_covers_the_window():
    text = rendered()
    assert "0.1" in text and "0.2" in text


def test_braces_balance():
    text = rendered()
    assert text.count("{") == text.count("}")


def test_crlf_endings():
    assert "\r\n" in rendered()


def test_at_most_available_colour_ids():
    # More panes than real-file colour ids: they cycle, never crash.
    many = [{"expr": f"V(n{i})", "ymin": 0, "ymax": 1} for i in range(9)]
    text = render_plt("Transient Analysis", many, x_start=0, x_stop=1)
    assert "Npanes: 9" in text
