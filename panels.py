"""Panel UI -- connections list/connect form + sObjects overview.

SIDEBAR CONTENT -- NO CARDS ANYWHERE, per ~/UI_INTERFACE_STANDARD.md's
"left sidebar, no decorated cards" rule (same convention as MuleSoft
Connector's / Power Automate Connector's / n8n Connector's panels.py).

Every section (connections, connect form, sObjects) is a plain ui.Stack,
content stacked vertically and left-aligned, sections separated by
ui.Divider() -- no Card border/background/shadow anywhere in this slot.
Disconnect lives only in the "App settings" screen (panels_settings.py).
The one secondary "App settings" button is always the LAST element at the
bottom of the sidebar.

WHY A FULL FORM, NOT A TOKEN LIKE n8n/Make.com/Slack.

Salesforce's OAuth 2.0 Client Credentials Flow needs a Connected App's
Consumer Key + Consumer Secret plus the org's own My Domain host (the
token endpoint and every subsequent API call target THAT host, not a
generic salesforce.com endpoint -- see salesforce_client.py's module
docstring). The form therefore asks for all three required fields plus
an optional label, with a help dialog explaining where to find each one
-- same shape as MuleSoft Connector's 4-field form.
"""
from __future__ import annotations

from imperal_sdk import ui

import salesforce_client as sc
from app import ext
import handlers as h


def _settings_button() -> ui.UINode:
    """The one required secondary entry point into the settings screen --
    always the last element at the bottom of the sidebar."""
    return ui.Button(
        "App settings", variant="secondary", size="sm", full_width=True,
        icon="settings", on_click=ui.Call("__panel__salesforce_settings"),
    )


def _connection_row(c: dict) -> ui.UINode:
    label = c.get("label") or c.get("my_domain", "")
    return ui.Stack(direction="v", gap=1, children=[
        ui.Text(label, variant="body"),
        ui.Text(c.get("my_domain", ""), variant="caption"),
    ])


def _connections_section(connections: list[dict]) -> ui.UINode:
    if not connections:
        return ui.Text("No Salesforce organizations connected yet.", variant="caption")
    children: list[ui.UINode] = []
    for i, c in enumerate(connections):
        if i > 0:
            children.append(ui.Divider())
        children.append(_connection_row(c))
    return ui.Stack(direction="v", gap=2, children=children)


def _sobject_row(o: dict) -> ui.UINode:
    """One sObject row -- plain content, no Card wrapper, no padding/
    border, per Vlad's standing sidebar rule."""
    subtitle = o.get("name", "") + (" · custom" if o.get("custom") else "")
    return ui.Stack(direction="v", gap=1, children=[
        ui.Text(o.get("label", o.get("name", "")), variant="body"),
        ui.Text(subtitle, variant="caption"),
    ])


def _sobjects_section(objects: list[dict]) -> ui.UINode:
    if not objects:
        return ui.Text("No sObjects loaded yet.", variant="caption")
    children: list[ui.UINode] = []
    for i, o in enumerate(objects[:15]):
        if i > 0:
            children.append(ui.Divider())
        children.append(_sobject_row(o))
    return ui.Stack(direction="v", gap=2, children=children)


def _connect_section() -> ui.UINode:
    """Plain content, no Card wrapper. Stretched full-width per
    UI_INTERFACE_STANDARD.md (2026-08-20). No intro heading/description
    text here -- the Connected App walkthrough lives ONLY in
    salesforce_connect_help's modal (button below opens it); repeating it
    here would duplicate that instruction."""
    return ui.Stack(direction="v", gap=3, align="stretch", children=[
        ui.Button("How do I set this up?", variant="ghost", size="sm",
                  icon="HelpCircle",
                  on_click=ui.Call("__panel__salesforce_connect_help")),
        ui.Form(
            action="connect_salesforce",
            submit_label="Verify and connect",
            children=[
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("Consumer Key (Client ID)", variant="caption"),
                    ui.Input(param_name="client_id", placeholder="Connected App Consumer Key"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("Consumer Secret (Client Secret)", variant="caption"),
                    ui.Password(param_name="client_secret",
                                 placeholder="Connected App Consumer Secret"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("My Domain host", variant="caption"),
                    ui.Input(param_name="my_domain", placeholder="mycompany.my.salesforce.com"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("Label (optional)", variant="caption"),
                    ui.Input(param_name="label", placeholder="e.g. Production"),
                ]),
            ],
        ),
    ])


@ext.panel("salesforce_connect", slot="left", title="Salesforce", icon="☁️",
           default_width=320, min_width=260, max_width=420)
async def salesforce_connect_panel(ctx, **kwargs) -> object:
    connections = await h._load_connections(ctx)
    connected = bool(connections)

    header = ui.Header(text="Salesforce", level=2,
                        subtitle="Manage your Salesforce org's records, reports, and automation from Imperal")

    if not connected:
        return ui.Stack(direction="v", gap=4, align="stretch", children=[
            header,
            _connect_section(),
            ui.Divider(),
            _settings_button(),
        ])

    objects: list[dict] = []
    first = connections[0]
    try:
        tok = await sc.get_access_token(ctx, first["client_id"], first["client_secret"], first["my_domain"])
        if tok.get("ok"):
            objects = await sc.list_sobjects(ctx, tok["access_token"], tok["instance_url"])
    except sc.ClientFail:
        objects = []

    return ui.Stack(direction="v", gap=4, align="stretch", children=[
        header,
        ui.Text("Connected organizations", variant="subtitle"),
        _connections_section(connections),
        ui.Divider(),
        _connect_section(),
        ui.Divider(),
        ui.Text(f"sObjects -- {first.get('label') or first.get('my_domain', '')}", variant="subtitle"),
        _sobjects_section(objects),
        ui.Divider(),
        _settings_button(),
    ])


@ext.panel("salesforce_connect_help", slot="center",
           title="How to connect Salesforce", center_overlay=True)
async def salesforce_connect_help(ctx, **kwargs) -> object:
    content = ui.Stack(direction="v", gap=3, children=[
        ui.Text("1. In Salesforce Setup, open App Manager > New Connected App."),
        ui.Text("2. Enable OAuth Settings, then enable \"Client Credentials Flow\" under the OAuth policy."),
        ui.Text("3. Set a \"Run As\" integration user -- every API call this connector makes acts as that user, so its permission set decides what's reachable."),
        ui.Text("4. Save, then open the Connected App's \"Manage Consumer Details\" to copy the Consumer Key and Consumer Secret."),
        ui.Text("5. Copy your org's My Domain host from Setup > My Domain (e.g. mycompany.my.salesforce.com)."),
        ui.Divider(),
        ui.Alert(
            title="Permissions follow the integration user",
            message=(
                "This connector can only do what the Connected App's \"Run "
                "As\" user's profile/permission set allows. Grant access to "
                "specific objects/fields there, not here."
            ),
            type="warning",
        ),
        ui.Divider(),
        ui.Link(
            label="Open Salesforce's official Client Credentials Flow guide",
            href="https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_client_credentials_flow.htm",
        ),
    ])
    return ui.Dialog(
        title="How to connect Salesforce",
        content=content,
        confirm_label="",
        cancel_label="Close",
    )


@ext.panel("salesforce_center", slot="center", title="Salesforce", icon="☁️", center_overlay=True)
async def salesforce_center_panel(ctx, **kwargs) -> object:
    """Base center panel -- per UI_INTERFACE_STANDARD.md (2026-08-20).
    This app has no list/detail content of its own to show in the center
    by default (everything lives in the sidebar). MUST carry
    center_overlay=True: per docs.imperal.io/en/concepts/panels, a plain
    slot="center" panel is registered but the Panel app never fetches it
    at session-init without that flag. Text is the shared canonical
    wording -- must stay identical across every app in this situation."""
    return ui.Empty(
        message="Nothing to show here -- this app is managed entirely from the sidebar.",
        icon="👈",
    )
