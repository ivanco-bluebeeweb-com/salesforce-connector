"""The single 'App settings' screen (center slot) -- connection management
(disconnect per Salesforce organization) for Salesforce Connector. Split
out of panels.py per the same convention as MuleSoft's / Aidentika's /
n8n's / Power Automate Connector's panels_settings.py.

Per ~/UI_INTERFACE_STANDARD.md: the left sidebar never wraps the connect
form in a Card, and disconnect (never exposed in the sidebar itself) lives
here, one row per connected organization. The one secondary "App settings"
button sits LAST at the bottom of the sidebar.
"""
from __future__ import annotations

from imperal_sdk import ui

from app import ext
import handlers as h


def _connection_row(c: dict) -> ui.UINode:
    label = c.get("title") or c.get("detail", "")
    return ui.Stack(direction="v", gap=1, align="start", children=[
        ui.Text(label, variant="body"),
        ui.Text(c.get("detail", ""), variant="caption"),
        ui.Button(
            "Disconnect", variant="danger", size="sm",
            on_click=ui.Call("disconnect_salesforce", {"connection_id": c.get("id")}),
        ),
    ])


def _connections_section(connections: list[dict]) -> ui.UINode:
    if not connections:
        return ui.Stack(direction="v", gap=1, children=[
            ui.Text("Connections", variant="heading"),
            ui.Text("No Salesforce organizations connected yet.", variant="caption"),
        ])
    children: list[ui.UINode] = [ui.Text("Connections", variant="heading")]
    for i, c in enumerate(connections):
        if i > 0:
            children.append(ui.Divider())
        children.append(_connection_row(c))
    return ui.Stack(direction="v", gap=2, align="start", children=children)


@ext.panel("salesforce_settings", slot="center")
async def salesforce_settings_panel(ctx) -> ui.UINode:
    raw_connections = await h._load_connections(ctx)
    views = [h._connection_view(c) for c in raw_connections]
    return ui.Stack(direction="v", gap=3, align="start", children=[
        _connections_section(views),
    ])
