"""Salesforce HTTP client -- OAuth2 client-credentials auth against a
user's own Connected App, thin wrappers around REST API (sObjects, SOQL/
SOSL, Composite), Bulk API 2.0, Connect REST API (Chatter/Files), Reports
& Dashboards API, Process/Approval API, and Platform Events.

WHY CLIENT CREDENTIALS (Connected App + \"Run As\" integration user), NOT
DELEGATED USER OAUTH -- see app.py module docstring for the full
architectural reasoning. Token is requested against the org's own My
Domain host with grant_type=client_credentials
(help.salesforce.com/.../remoteaccess_oauth_client_credentials_flow,
confirmed 2026-08-20).

WHY instance_url MUST BE STORED PER-CONNECTION, UNLIKE MOST OTHER
CONNECTORS' FIXED API HOST.

Every other BYOK connector in this portfolio (MuleSoft: anypoint.
mulesoft.com, Power Automate: a fixed Microsoft host) talks to one fixed
API host. Salesforce is different: the token response itself returns an
`instance_url` specific to the CALLING org (e.g.
https://mycompany.my.salesforce.com), and every subsequent REST/Bulk/
Connect call must be made against THAT host, not a generic salesforce.com
endpoint. This is why my_domain is asked at connect time (to know where
to request the token) and instance_url is cached alongside the connection
(to know where to send every other call).

WHY 401 vs 403 ARE HANDLED DIFFERENTLY, SAME PRINCIPLE AS MuleSoft/n8n/
Make.com/Power Automate CONNECTOR's clients.

A 401 (INVALID_SESSION_ID) means the access token itself is invalid or
expired -- wrong credentials, or the org revoked the Connected App. A 403
(INSUFFICIENT_ACCESS_OR_READONLY / REQUEST_LIMIT_EXCEEDED as 403 in some
paths) means the token is valid but this integration user lacks the
Salesforce permission (profile/permission set) for this specific object
or field -- a materially different, more fixable cause (grant access in
Setup, not re-enter credentials).
"""
from __future__ import annotations

API_VERSION = "v62.0"

ACCOUNT_MISSING = "SALESFORCE_ACCOUNT_MISSING"
TOKEN_REJECTED = "SALESFORCE_TOKEN_REJECTED"
PERMISSION_DENIED = "SALESFORCE_PERMISSION_DENIED"
NOT_FOUND = "SALESFORCE_NOT_FOUND"
VALIDATION_FAILED = "SALESFORCE_VALIDATION_FAILED"
RESPONSE_UNEXPECTED = "SALESFORCE_RESPONSE_UNEXPECTED"
UNREACHABLE = "SALESFORCE_UNREACHABLE"
RATE_LIMITED = "SALESFORCE_RATE_LIMITED"
BACKEND_5XX = "SALESFORCE_BACKEND_5XX"
BACKEND_TIMEOUT = "SALESFORCE_BACKEND_TIMEOUT"

_MESSAGES = {
    ACCOUNT_MISSING: "No Salesforce organization is connected yet.",
    TOKEN_REJECTED: "Salesforce rejected these credentials. Check the Consumer Key/Secret and My Domain, then reconnect.",
    PERMISSION_DENIED: "Salesforce accepted the credentials, but the Connected App's integration user lacks permission for this object/field. Grant it via a Permission Set in Setup.",
    NOT_FOUND: "Salesforce has no such record/object, or this org cannot access it.",
    VALIDATION_FAILED: "Salesforce rejected the request as invalid.",
    RESPONSE_UNEXPECTED: "Salesforce returned a response the connector could not safely interpret.",
    UNREACHABLE: "Could not reach Salesforce.",
    RATE_LIMITED: "Salesforce is rate-limiting requests (API limit); try again shortly.",
    BACKEND_5XX: "Salesforce returned a server error; try again shortly.",
    BACKEND_TIMEOUT: "Salesforce took too long to respond; try again shortly.",
}
_RETRYABLE = {RATE_LIMITED, BACKEND_5XX, BACKEND_TIMEOUT}


def fail(code: str, detail: str = "") -> dict:
    message = _MESSAGES.get(code, code)
    if detail:
        message = f"{message} ({detail})"
    return {"ok": False, "error_code": code, "error": message, "retryable": code in _RETRYABLE}


class ClientFail(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("error", "Salesforce request failed"))
        self.payload = payload


async def get_access_token(ctx, client_id: str, client_secret: str, my_domain: str) -> dict:
    """Client-credentials token request against the org's own My Domain
    token endpoint. Returns {"ok": True, "access_token": ..., "instance_url":
    ...} or a fail() dict. instance_url is Salesforce-specific -- it MUST
    be cached and used for every subsequent call (see module docstring)."""
    domain = my_domain.strip().rstrip("/")
    if domain.startswith("http://") or domain.startswith("https://"):
        domain = domain.split("://", 1)[1]
    if not domain:
        return fail(VALIDATION_FAILED, "my_domain is required")
    token_url = f"https://{domain}/services/oauth2/token"
    resp = await ctx.http.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code in (400, 401):
        return fail(TOKEN_REJECTED)
    if resp.status_code >= 500:
        return fail(BACKEND_5XX)
    if resp.status_code != 200:
        return fail(RESPONSE_UNEXPECTED, f"token endpoint returned {resp.status_code}")
    body = resp.body if isinstance(resp.body, dict) else {}
    token = body.get("access_token")
    instance_url = body.get("instance_url")
    if not token or not instance_url:
        return fail(RESPONSE_UNEXPECTED, "token response missing access_token/instance_url")
    return {"ok": True, "access_token": token, "instance_url": instance_url}


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _check_status(resp, action: str) -> dict | list:
    if resp.status_code in (200, 201, 202, 204):
        if resp.status_code == 204:
            return {}
        return resp.body if isinstance(resp.body, (dict, list)) else {}
    if resp.status_code == 401:
        raise ClientFail(fail(TOKEN_REJECTED, action))
    if resp.status_code == 403:
        raise ClientFail(fail(PERMISSION_DENIED, action))
    if resp.status_code == 404:
        raise ClientFail(fail(NOT_FOUND, action))
    if resp.status_code == 429:
        raise ClientFail(fail(RATE_LIMITED, action))
    if resp.status_code >= 500:
        raise ClientFail(fail(BACKEND_5XX, action))
    if resp.status_code == 400:
        # Salesforce often returns a list of {errorCode, message} on 400s.
        detail = action
        if isinstance(resp.body, list) and resp.body:
            first = resp.body[0]
            if isinstance(first, dict):
                detail = f"{action}: {first.get('errorCode', '')} {first.get('message', '')}".strip()
        raise ClientFail(fail(VALIDATION_FAILED, detail))
    raise ClientFail(fail(RESPONSE_UNEXPECTED, f"{action}: HTTP {resp.status_code}"))


async def check_connection(ctx, client_id: str, client_secret: str, my_domain: str) -> dict:
    """Get a token, then a cheap GET to /services/data to prove the org
    and API version are actually reachable."""
    tok = await get_access_token(ctx, client_id, client_secret, my_domain)
    if not tok.get("ok"):
        return tok
    resp = await ctx.http.get(
        f"{tok['instance_url']}/services/data/{API_VERSION}/limits",
        headers=_headers(tok["access_token"]),
    )
    try:
        _check_status(resp, "verify connection")
    except ClientFail as e:
        return e.payload
    return {"ok": True, "instance_url": tok["instance_url"]}


# ─────────────────────────────────────────────────────────────────────────
# Generic sObject CRUD
# ─────────────────────────────────────────────────────────────────────────


def _sobjects_url(instance_url: str, object_type: str, record_id: str = "") -> str:
    base = f"{instance_url}/services/data/{API_VERSION}/sobjects/{object_type}"
    return f"{base}/{record_id}" if record_id else base


async def create_record(ctx, access_token: str, instance_url: str, object_type: str, fields: dict) -> dict:
    resp = await ctx.http.post(
        _sobjects_url(instance_url, object_type),
        headers=_headers(access_token),
        json=fields,
    )
    return _check_status(resp, f"create {object_type}")


async def get_record(ctx, access_token: str, instance_url: str, object_type: str, record_id: str, fields: str = "") -> dict:
    params = {"fields": fields} if fields else None
    resp = await ctx.http.get(
        _sobjects_url(instance_url, object_type, record_id),
        headers=_headers(access_token),
        params=params,
    )
    return _check_status(resp, f"get {object_type}/{record_id}")


async def update_record(ctx, access_token: str, instance_url: str, object_type: str, record_id: str, fields: dict) -> dict:
    resp = await ctx.http.patch(
        _sobjects_url(instance_url, object_type, record_id),
        headers=_headers(access_token),
        json=fields,
    )
    return _check_status(resp, f"update {object_type}/{record_id}")


async def upsert_record(
    ctx, access_token: str, instance_url: str, object_type: str,
    external_id_field: str, external_id_value: str, fields: dict,
) -> dict:
    url = f"{instance_url}/services/data/{API_VERSION}/sobjects/{object_type}/{external_id_field}/{external_id_value}"
    resp = await ctx.http.patch(url, headers=_headers(access_token), json=fields)
    return _check_status(resp, f"upsert {object_type}/{external_id_field}={external_id_value}")


async def delete_record(ctx, access_token: str, instance_url: str, object_type: str, record_id: str) -> dict:
    resp = await ctx.http.delete(
        _sobjects_url(instance_url, object_type, record_id),
        headers=_headers(access_token),
    )
    return _check_status(resp, f"delete {object_type}/{record_id}")


async def describe_sobject(ctx, access_token: str, instance_url: str, object_type: str) -> dict:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/sobjects/{object_type}/describe",
        headers=_headers(access_token),
    )
    return _check_status(resp, f"describe {object_type}")


async def list_sobjects(ctx, access_token: str, instance_url: str) -> list[dict]:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/sobjects",
        headers=_headers(access_token),
    )
    body = _check_status(resp, "list sobjects")
    return body.get("sobjects", []) if isinstance(body, dict) else []


# ─────────────────────────────────────────────────────────────────────────
# SOQL / SOSL query + Composite/Batch
# ─────────────────────────────────────────────────────────────────────────


async def run_soql(ctx, access_token: str, instance_url: str, query: str, include_deleted: bool = False) -> dict:
    path = "queryAll" if include_deleted else "query"
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/{path}",
        headers=_headers(access_token),
        params={"q": query},
    )
    return _check_status(resp, "run SOQL query")


async def continue_soql(ctx, access_token: str, instance_url: str, next_records_url: str) -> dict:
    url = next_records_url if next_records_url.startswith("http") else f"{instance_url}{next_records_url}"
    resp = await ctx.http.get(url, headers=_headers(access_token))
    return _check_status(resp, "continue SOQL query")


async def run_sosl(ctx, access_token: str, instance_url: str, search: str) -> dict:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/search",
        headers=_headers(access_token),
        params={"q": search},
    )
    return _check_status(resp, "run SOSL search")


async def run_composite(ctx, access_token: str, instance_url: str, sub_requests: list[dict], all_or_none: bool = False) -> dict:
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/composite",
        headers=_headers(access_token),
        json={"allOrNone": all_or_none, "compositeRequest": sub_requests},
    )
    return _check_status(resp, "run composite request")


# ─────────────────────────────────────────────────────────────────────────
# Bulk API 2.0 -- async job-based mass insert/update/upsert/delete/query
# ─────────────────────────────────────────────────────────────────────────


def _bulk_headers(access_token: str, csv: bool = False) -> dict:
    h = _headers(access_token)
    if csv:
        h["Content-Type"] = "text/csv"
    return h


async def create_bulk_job(
    ctx, access_token: str, instance_url: str, object_type: str, operation: str,
    external_id_field: str = "", csv_data: str = "", query: str = "",
) -> dict:
    if operation in ("query", "queryAll"):
        resp = await ctx.http.post(
            f"{instance_url}/services/data/{API_VERSION}/jobs/query",
            headers=_headers(access_token),
            json={"operation": operation, "query": query},
        )
        job = _check_status(resp, "create bulk query job")
        return job
    payload: dict = {"object": object_type, "operation": operation, "contentType": "CSV"}
    if operation == "upsert" and external_id_field:
        payload["externalIdFieldName"] = external_id_field
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/jobs/ingest",
        headers=_headers(access_token),
        json=payload,
    )
    job = _check_status(resp, "create bulk job")
    if csv_data and isinstance(job, dict) and job.get("id"):
        upload_resp = await ctx.http.put(
            f"{instance_url}/services/data/{API_VERSION}/jobs/ingest/{job['id']}/batches",
            headers=_bulk_headers(access_token, csv=True),
            content=csv_data,
        )
        _check_status(upload_resp, "upload bulk job batch")
        close_resp = await ctx.http.patch(
            f"{instance_url}/services/data/{API_VERSION}/jobs/ingest/{job['id']}",
            headers=_headers(access_token),
            json={"state": "UploadComplete"},
        )
        _check_status(close_resp, "close bulk job for processing")
    return job


async def get_bulk_job(ctx, access_token: str, instance_url: str, job_id: str, query_job: bool = False) -> dict:
    family = "query" if query_job else "ingest"
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/jobs/{family}/{job_id}",
        headers=_headers(access_token),
    )
    return _check_status(resp, "get bulk job")


async def list_bulk_jobs(ctx, access_token: str, instance_url: str, query_job: bool = False) -> list[dict]:
    family = "query" if query_job else "ingest"
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/jobs/{family}",
        headers=_headers(access_token),
    )
    body = _check_status(resp, "list bulk jobs")
    return body.get("records", []) if isinstance(body, dict) else []


async def abort_bulk_job(ctx, access_token: str, instance_url: str, job_id: str, query_job: bool = False) -> dict:
    family = "query" if query_job else "ingest"
    resp = await ctx.http.patch(
        f"{instance_url}/services/data/{API_VERSION}/jobs/{family}/{job_id}",
        headers=_headers(access_token),
        json={"state": "Aborted"},
    )
    return _check_status(resp, "abort bulk job")


# ─────────────────────────────────────────────────────────────────────────
# Lead conversion -- a dedicated Salesforce process endpoint
# ─────────────────────────────────────────────────────────────────────────


async def convert_lead(
    ctx, access_token: str, instance_url: str, lead_id: str, converted_status: str,
    create_opportunity: bool = True, opportunity_name: str = "",
    account_id: str = "", contact_id: str = "",
) -> dict:
    payload: dict = {
        "leadId": lead_id,
        "convertedStatus": converted_status,
        "doNotCreateOpportunity": not create_opportunity,
    }
    if opportunity_name:
        payload["opportunityName"] = opportunity_name
    if account_id:
        payload["accountId"] = account_id
    if contact_id:
        payload["contactId"] = contact_id
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/actions/standard/convertLead",
        headers=_headers(access_token),
        json=[payload],
    )
    body = _check_status(resp, "convert lead")
    return body[0] if isinstance(body, list) and body else body


# ─────────────────────────────────────────────────────────────────────────
# Connect REST API -- Chatter (feed) + Files
# ─────────────────────────────────────────────────────────────────────────


async def post_chatter_feed(ctx, access_token: str, instance_url: str, record_id: str, text: str) -> dict:
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/chatter/feed-elements",
        headers=_headers(access_token),
        json={
            "body": {"messageSegments": [{"type": "Text", "text": text}]},
            "feedElementType": "FeedItem",
            "subjectId": record_id,
        },
    )
    return _check_status(resp, "post Chatter feed item")


async def comment_on_feed(ctx, access_token: str, instance_url: str, feed_item_id: str, text: str) -> dict:
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/chatter/feed-elements/{feed_item_id}/capabilities/comments/items",
        headers=_headers(access_token),
        json={"body": {"messageSegments": [{"type": "Text", "text": text}]}},
    )
    return _check_status(resp, "comment on feed item")


async def list_chatter_feed(ctx, access_token: str, instance_url: str, record_id: str) -> dict:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/chatter/feeds/record/{record_id}/feed-elements",
        headers=_headers(access_token),
    )
    return _check_status(resp, "list Chatter feed")


async def upload_file(
    ctx, access_token: str, instance_url: str, title: str, file_content_base64: str,
    path_on_client: str = "", record_id: str = "",
) -> dict:
    cv = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/sobjects/ContentVersion",
        headers=_headers(access_token),
        json={
            "Title": title,
            "PathOnClient": path_on_client or title,
            "VersionData": file_content_base64,
        },
    )
    result = _check_status(cv, "upload file (ContentVersion)")
    if record_id and isinstance(result, dict) and result.get("id"):
        cv_id = result["id"]
        get_resp = await ctx.http.get(
            f"{instance_url}/services/data/{API_VERSION}/sobjects/ContentVersion/{cv_id}",
            headers=_headers(access_token),
            params={"fields": "ContentDocumentId"},
        )
        cv_detail = _check_status(get_resp, "read uploaded ContentVersion")
        doc_id = cv_detail.get("ContentDocumentId") if isinstance(cv_detail, dict) else None
        if doc_id:
            link_resp = await ctx.http.post(
                f"{instance_url}/services/data/{API_VERSION}/sobjects/ContentDocumentLink",
                headers=_headers(access_token),
                json={"ContentDocumentId": doc_id, "LinkedEntityId": record_id, "ShareType": "V"},
            )
            _check_status(link_resp, "link file to record")
    return result


async def list_record_files(ctx, access_token: str, instance_url: str, record_id: str) -> list[dict]:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/query",
        headers=_headers(access_token),
        params={
            "q": (
                f"SELECT ContentDocumentId, ContentDocument.Title, ContentDocument.FileType "
                f"FROM ContentDocumentLink WHERE LinkedEntityId = '{record_id}'"
            )
        },
    )
    body = _check_status(resp, "list record files")
    return body.get("records", []) if isinstance(body, dict) else []


# ─────────────────────────────────────────────────────────────────────────
# Reports & Dashboards API (read-only surface)
# ─────────────────────────────────────────────────────────────────────────


async def list_reports(ctx, access_token: str, instance_url: str) -> list[dict]:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/analytics/reports",
        headers=_headers(access_token),
    )
    body = _check_status(resp, "list reports")
    return body if isinstance(body, list) else []


async def run_report(ctx, access_token: str, instance_url: str, report_id: str) -> dict:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/analytics/reports/{report_id}",
        headers=_headers(access_token),
    )
    return _check_status(resp, "run report")


async def list_dashboards(ctx, access_token: str, instance_url: str) -> list[dict]:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/analytics/dashboards",
        headers=_headers(access_token),
    )
    body = _check_status(resp, "list dashboards")
    return body if isinstance(body, list) else []


async def get_dashboard(ctx, access_token: str, instance_url: str, dashboard_id: str) -> dict:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/analytics/dashboards/{dashboard_id}",
        headers=_headers(access_token),
    )
    return _check_status(resp, "get dashboard")


# ─────────────────────────────────────────────────────────────────────────
# Approval Process API
# ─────────────────────────────────────────────────────────────────────────


async def submit_for_approval(ctx, access_token: str, instance_url: str, record_id: str, comments: str = "") -> dict:
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/process/approvals",
        headers=_headers(access_token),
        json={"requests": [{"actionType": "Submit", "contextId": record_id, "comments": comments}]},
    )
    return _check_status(resp, "submit for approval")


async def process_approval(
    ctx, access_token: str, instance_url: str, work_item_id: str, action: str, comments: str = "",
) -> dict:
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/process/approvals",
        headers=_headers(access_token),
        json={"requests": [{"actionType": action, "workItemId": work_item_id, "comments": comments}]},
    )
    return _check_status(resp, f"{action.lower()} approval")


async def list_approval_work_items(ctx, access_token: str, instance_url: str) -> list[dict]:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/query",
        headers=_headers(access_token),
        params={"q": "SELECT Id, ProcessInstance.TargetObjectId, ActorId FROM ProcessInstanceWorkitem"},
    )
    body = _check_status(resp, "list approval work items")
    return body.get("records", []) if isinstance(body, dict) else []


# ─────────────────────────────────────────────────────────────────────────
# Transactional email + org limits + Platform Events
# ─────────────────────────────────────────────────────────────────────────


async def send_email(
    ctx, access_token: str, instance_url: str, to_addresses: list[str], subject: str, body: str,
    related_record_id: str = "", save_as_activity: bool = True,
) -> dict:
    email: dict = {
        "toAddresses": to_addresses,
        "subject": subject,
        "plainTextBody": body,
        "saveAsActivity": save_as_activity,
    }
    if related_record_id:
        email["whatId"] = related_record_id
    resp = await ctx.http.post(
        f"{instance_url}/services/data/{API_VERSION}/actions/standard/emailSimple",
        headers=_headers(access_token),
        json=[{"inputs": [email]}],
    )
    result = _check_status(resp, "send email")
    return result[0] if isinstance(result, list) and result else result


async def get_org_limits(ctx, access_token: str, instance_url: str) -> dict:
    resp = await ctx.http.get(
        f"{instance_url}/services/data/{API_VERSION}/limits",
        headers=_headers(access_token),
    )
    return _check_status(resp, "get org limits")


async def publish_platform_event(ctx, access_token: str, instance_url: str, event_type: str, fields: dict) -> dict:
    resp = await ctx.http.post(
        _sobjects_url(instance_url, event_type),
        headers=_headers(access_token),
        json=fields,
    )
    return _check_status(resp, f"publish platform event {event_type}")


# ─────────────────────────────────────────────────────────────────────────
# Tier 3 value-add: bulk connector-level operations + org health audit,
# same shape as MuleSoft/Automation Anywhere/UiPath/Blue Prism connectors.
# ─────────────────────────────────────────────────────────────────────────


async def bulk_delete_records(ctx, access_token: str, instance_url: str, object_type: str, record_ids: list[str]) -> list[dict]:
    results = []
    for rid in record_ids:
        try:
            await delete_record(ctx, access_token, instance_url, object_type, rid)
            results.append({"record_id": rid, "ok": True})
        except ClientFail as e:
            results.append({"record_id": rid, "ok": False, "error": e.payload.get("error")})
    return results


async def bulk_update_records(ctx, access_token: str, instance_url: str, object_type: str, record_ids: list[str], fields: dict) -> list[dict]:
    results = []
    for rid in record_ids:
        try:
            await update_record(ctx, access_token, instance_url, object_type, rid, fields)
            results.append({"record_id": rid, "ok": True})
        except ClientFail as e:
            results.append({"record_id": rid, "ok": False, "error": e.payload.get("error")})
    return results


async def get_bulk_job_results(ctx, access_token: str, instance_url: str, job_id: str, result_type: str = "successfulResults") -> str:
    """Bulk API 2.0 result endpoints return raw CSV text, not JSON."""
    if result_type == "results":
        url = f"{instance_url}/services/data/{API_VERSION}/jobs/query/{job_id}/results"
    else:
        path_map = {
            "successfulResults": "successfulResults",
            "failedResults": "failedResults",
            "unprocessedrecords": "unprocessedrecords",
        }
        segment = path_map.get(result_type, "successfulResults")
        url = f"{instance_url}/services/data/{API_VERSION}/jobs/ingest/{job_id}/{segment}"
    resp = await ctx.http.get(url, headers=_headers(access_token))
    if resp.status_code == 401:
        raise ClientFail(fail(TOKEN_REJECTED))
    if resp.status_code == 403:
        raise ClientFail(fail(PERMISSION_DENIED))
    if resp.status_code == 404:
        raise ClientFail(fail(NOT_FOUND, "bulk job"))
    if resp.status_code >= 500:
        raise ClientFail(fail(BACKEND_5XX))
    if resp.status_code not in (200, 204):
        raise ClientFail(fail(RESPONSE_UNEXPECTED, f"bulk job results returned {resp.status_code}"))
    return resp.text if hasattr(resp, "text") else (resp.body if isinstance(resp.body, str) else "")


async def audit_org(ctx, access_token: str, instance_url: str) -> dict:
    """Aggregated org-health snapshot: API usage limits + a handful of
    headline record counts, same value-add shape as MuleSoft's
    audit_cloudhub_environment / UiPath's audit_folder."""
    rows: list[dict] = []
    try:
        limits = await get_org_limits(ctx, access_token, instance_url)
        daily = limits.get("DailyApiRequests", {}) if isinstance(limits, dict) else {}
        remaining = daily.get("Remaining", "?")
        maximum = daily.get("Max", "?")
        rows.append({"check": "Daily API requests", "status": "ok", "detail": f"{remaining} / {maximum} remaining"})
    except ClientFail as e:
        rows.append({"check": "Daily API requests", "status": "error", "detail": e.payload.get("error", "")})
    for obj in ("Account", "Contact", "Lead", "Opportunity", "Case"):
        try:
            body = await run_soql(ctx, access_token, instance_url, f"SELECT COUNT() FROM {obj}")
            total = body.get("totalSize", 0) if isinstance(body, dict) else 0
            rows.append({"check": f"{obj} records", "status": "ok", "detail": str(total)})
        except ClientFail as e:
            rows.append({"check": f"{obj} records", "status": "error", "detail": e.payload.get("error", "")})
    return {"rows": rows}
