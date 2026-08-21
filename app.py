"""Extension declaration, secrets, lifecycle hooks.

WHY BYOK (bring-your-own-key), same reasoning as MuleSoft Connector /
Power Automate Connector / Make.com Connector / n8n Connector. The user's
Salesforce organization is THEIRS -- Imperal cannot and should not broker
access to someone else's org centrally.

WHY CLIENT CREDENTIALS FLOW (Connected App), NOT DELEGATED USER OAUTH,
AND NOT THE PLATFORM'S BUILT-IN ext.oauth.

Salesforce is not among the platform's built-in ext.oauth providers
(google/microsoft/yahoo only -- ctx.oauth_authorize_url raises ValueError
on anything else, confirmed in Docs/imperal-docs/llms-full.txt during
Discovery 2026-08-20). Two real alternatives exist for a non-built-in
provider: (a) a hand-rolled `@ext.webhook("/callback")` redirect dance, or
(b) a grant type that needs no redirect at all. Salesforce's own OAuth 2.0
Client Credentials Flow (help.salesforce.com/.../
remoteaccess_oauth_client_credentials_flow, supported since 2023,
confirmed during Discovery) is exactly (b): a Connected App configured
with a "Run As" integration user lets the connector authenticate with
just client_id + client_secret + the org's My Domain URL -- no browser
redirect, no callback endpoint, same shape already proven working for
MuleSoft's Anypoint Connected App and Power Automate's Azure AD App
Registration. This is simpler and safer than the hand-rolled webhook
escape hatch, so it is the one used here. (Salesforce's older
Username-Password Flow, grant_type=password, is explicitly being retired
in Winter '27 per Discovery -- never use it.)

WHY `write_mode="both"`, SAME REASONING AS MuleSoft/n8n/Make.com/Power
Automate CONNECTOR.

Declaring `write_mode="user"` would mean only the platform's generic
Secrets screen could write these -- leaving a first-time user with no
in-app screen explaining what a Connected App even is or how to create
one. `"both"` keeps the generic Secrets screen as a fallback while
letting `connect_salesforce` be the friendly guided path.

WHY SCOPE IS PER-ACCOUNT, NOT APP-LEVEL, SAME AS MuleSoft/n8n/Make.com/
Power Automate CONNECTOR.

Each user connects their OWN Salesforce organization(s) -- these are not
developer-owned app credentials, so the connections secret is declared
per-account (default scope), not `scope="app"`.

WHY ONE SECRET HOLDING A JSON ARRAY, NOT FLAT SECRETS PER ORG.

A user may connect several orgs (e.g. Sandbox + Production, or several
client orgs for an agency). One secret holding a JSON array of
`{id, client_id, client_secret, my_domain, label}` objects lets
`connect_salesforce` append a new org without redeclaring secrets, same
pattern as MuleSoft's `mulesoft_connections` / Power Automate's
`power_automate_connections`.

WHY GENERIC sObject CRUD IS THE BACKBONE, NOT 20+ HAND-WRITTEN PER-OBJECT
FUNCTIONS.

Salesforce's own REST API is itself generic: every standard AND custom
object (Account, Contact, `MyCustomObject__c`, ...) is reached through the
exact same `/sobjects/{ObjectType}/...` shape (confirmed
using_resources_working_with_records.htm). Building named tools
(create_account, create_contact, ...) that each hand-roll their own HTTP
call would both duplicate code and silently fail to support any custom
object the user's org defines. Instead, `salesforce_client.py` exposes
one generic engine (create/get/update/upsert/delete/list/describe by
object_type), and the named CRM tools (Account/Contact/Lead/Opportunity/
Case/Task/Event/Campaign) are thin, discoverable wrappers over it --
giving both a good chat/tool-search experience AND full custom-object
coverage via `create_record`/`update_record`/etc. with an explicit
`object_type`.
"""

from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "salesforce-connector",
    version="0.1.0",
    display_name="Salesforce",
    description=(
        "Connect your own Salesforce organization to manage CRM data from "
        "Imperal -- Accounts, Contacts, Leads, Opportunities, Cases, "
        "Tasks/Events, Campaigns, and any custom object via generic "
        "record tools. Run SOQL/SOSL queries and composite/batch "
        "requests, bulk-import or bulk-update via Bulk API 2.0, post to "
        "Chatter and attach files, read Reports & Dashboards, drive "
        "Approval Processes, send email, publish Platform Events, and "
        "audit your org (pipeline snapshot, stale opportunities, org "
        "health). Uses your own Salesforce Connected App (OAuth 2.0 "
        "Client Credentials Flow) -- nothing is hosted or proxied by "
        "Imperal beyond the request itself. Note: this manages CRM data "
        "and declarative processes only; Metadata API deployments, Apex "
        "development (Tooling API), and real-time Streaming/Pub-Sub "
        "subscriptions are out of scope."
    ),
    icon="icon.svg",
    capabilities=[
        "salesforce:read",
        "salesforce:write",
    ],
    actions_explicit=True,
    system=False,
)

chat = ChatExtension(
    ext,
    tool_name="salesforce",
    description=(
        "Salesforce Connector -- connect your Salesforce organization via "
        "your own Connected App (Client Credentials Flow), then manage "
        "Accounts/Contacts/Leads/Opportunities/Cases/Tasks/Events/"
        "Campaigns and any custom object, run SOQL/SOSL queries, bulk "
        "operations via Bulk API 2.0, Chatter/Files, Reports/Dashboards, "
        "Approval Processes, email, Platform Events, and org audits "
        "(pipeline snapshot, stale opportunities)."
    ),
)

ext.secret(
    "salesforce_connections",
    (
        "JSON array of connected Salesforce organizations: "
        '[{"id": "...", "client_id": "...", "client_secret": "...", '
        '"my_domain": "mycompany.my.salesforce.com", "label": "..."}]. '
        "Each entry is one Connected App (Client Credentials Flow, "
        "\"Run As\" integration user configured) for one Salesforce org. "
        "Managed by connect_salesforce/disconnect_salesforce."
    ),
    write_mode="both",
)


@ext.health_check
def health_check(ctx) -> bool:
    return True
