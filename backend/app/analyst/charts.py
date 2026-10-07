"""Chart.js specification generator for visual procurement analytics."""

from __future__ import annotations

from typing import Any
from backend.app.analyst.models import ChartSpec


def build_chart_spec(
    title: str,
    chart_type: str,
    labels: list[str],
    datasets: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
) -> ChartSpec:
    """Construct a standardized Chart.js JSON specification from deterministic tool data."""
    # Standard color palette for procurement dashboards
    palette = [
        "rgba(54, 162, 235, 0.7)",   # Blue
        "rgba(75, 192, 192, 0.7)",   # Teal
        "rgba(255, 159, 64, 0.7)",   # Orange
        "rgba(153, 102, 255, 0.7)",  # Purple
        "rgba(255, 99, 132, 0.7)",   # Red
        "rgba(255, 205, 86, 0.7)",   # Yellow
        "rgba(201, 203, 207, 0.7)",  # Grey
    ]
    border_palette = [c.replace("0.7", "1.0") for c in palette]

    formatted_datasets = []
    for idx, ds in enumerate(datasets):
        ds_copy = dict(ds)
        if "backgroundColor" not in ds_copy:
            if chart_type in ("pie", "doughnut") and len(datasets) == 1:
                ds_copy["backgroundColor"] = palette[:len(labels)]
                ds_copy["borderColor"] = border_palette[:len(labels)]
            else:
                color_idx = idx % len(palette)
                ds_copy["backgroundColor"] = palette[color_idx]
                ds_copy["borderColor"] = border_palette[color_idx]
        if "borderWidth" not in ds_copy:
            ds_copy["borderWidth"] = 1.5
        formatted_datasets.append(ds_copy)

    chart_js_type = "bar"
    if chart_type == "line":
        chart_js_type = "line"
    elif chart_type in ("pie", "doughnut"):
        chart_js_type = "pie"
    elif chart_type == "scatter":
        chart_js_type = "scatter"

    full_spec = {
        "type": chart_js_type,
        "data": {
            "labels": labels,
            "datasets": formatted_datasets,
        },
        "options": options or {
            "responsive": True,
            "plugins": {
                "legend": {"position": "top"},
                "title": {"display": True, "text": title},
            },
            "scales": {
                "y": {"beginAtZero": True}
            } if chart_js_type not in ("pie", "doughnut") else {},
        },
    }

    norm_type: Any = chart_type if chart_type in ("bar", "grouped_bar", "line", "scatter", "stacked_bar", "pie") else "bar"

    return ChartSpec(
        title=title,
        type=norm_type,
        spec=full_spec,
    )
