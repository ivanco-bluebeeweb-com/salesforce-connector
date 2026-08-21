"""Chat functions for Salesforce Connector: connection management, generic
sObject CRUD, SOQL/SOSL, Composite, Bulk API 2.0, Lead conversion, Chatter/
Files, Reports/Dashboards, Approval, Email/Limits/Platform Events, and
Tier 3 bulk operations + org audit. Built on salesforce_client.py /
schemas.py, following the same shape as MuleSoft Connector's handlers.py.
"""
from __future__ import annotations

import uuid

from imperal_sdk import ActionResult

import salesforce_client as sc
from app import ext, chat
from schemas import (
    NoParams,
    ConnectSalesforceParams, ProviderConnection, ProviderConnectionList,
    DisconnectSalesforceParams, DeleteResult,
    CreateRecordParams, RecordResult,
    GetRecordParams, RecordDetail,
    UpdateRecordParams, DeleteRecordParams,
    UpsertRecordParams,
    DescribeObjectParams, ObjectDescribe, ObjectFieldMeta,
    ListObjectsParams, SObjectSummary, SObjectSummaryList,
    RunSoqlParams, SoqlRow, SoqlResult,
    ContinueSoqlParams,
    RunSoslParams, SoslResult,
    CompositeSubRequest, CompositeSubResult, RunCompositeParams, CompositeResult,
    CreateBulkJobParams, BulkJob, BulkJobList,
    GetBulkJobParams, ListBulkJobsParams,
    BulkJobResultsParams, BulkJobResults,
    AbortBulkJobParams,
    ConvertLeadParams, LeadConvertResult,
    PostChatterFeedParams, FeedPostResult,
    ListChatterFeedParams, FeedItem, ChatterFeedList,
    CommentOnFeedParams,
    UploadFileParams, FileUploadResult,
    ListRecordFilesParams, RecordFile, RecordFileList,
    ListReportsParams, ReportSummary, ReportList,
    RunReportParams, ReportRow, ReportResult,
    ListDashboardsParams, DashboardSummary, DashboardList,
    GetDashboardParams, DashboardComponent, DashboardDetail,
    SubmitForApprovalParams, ProcessApprovalParams, ApprovalActionResult,
    ListApprovalsParams, ApprovalWorkItem, ApprovalWorkItemList,
    SendEmailParams, SendEmailResult,
    GetOrgLimitsParams, OrgLimit, OrgLimitsResult,
    PublishPlatformEventParams, PlatformEventResult,
    BulkRecordIdsParams, BulkRecordResultItem, BulkRecordResult,
    AuditOrgParams, OrgAuditRow, OrgAuditReport,
)

_SECRET_NAME = "salesforce_connections"


async def _load_connections(ctx) -> list[dict]:
    raw = await ctx.secrets.get(_SECRET_NAME)
    return raw if isinstance(raw, list) else []


async def _save_connections(ctx, connections: list[dict]) -> None:
    await ctx.secrets.set(_SECRET_NAME, connections)


def _connection_view(c: dict) -> dict:
    return {
        "id": c.get("id", ""),
        "title": c.get("label") or c.get("my_domain", ""),
        "connected": True,
        "detail": c.get("my_domain", ""),
        "my_domain": c.get("my_domain", ""),
    }


async def _resolve_connection(ctx, connection_id: str = "") -> dict | None:
    connections = await _load_connections(ctx)
    if not connections:
        return None
    if connection_id:
        for c in connections:
            if c.get("id") == connection_id:
                return c
        return None
    return connections[0]


async def _get_access(ctx, connection_id: str = "") -> dict:
    """Resolve a connection then fetch a fresh access token. Returns
    {"ok": True, "access_token":..., "instance_url":...} or a fail dict."""
    conn = await _resolve_connection(ctx, connection_id)
    if not conn:
        return sc.fail(sc.VALIDATION_FAILED, "no Salesforce connection found -- connect one first")
    tok = await sc.get_access_token(ctx, conn["client_id"], conn["client_secret"], conn["my_domain"])
    return tok


@chat.function(
    "connect_salesforce",
    "Connect a Salesforce organization by saving its Connected App credentials "
    "(Consumer Key/Secret + My Domain host) for the OAuth 2.0 Client Credentials "
    "Flow, after checking they actually work. You'll need a Connected App with "
    "Client Credentials Flow enabled and a 'Run As' integration user configured "
    "(Setup > App Manager > New Connected App).",
    action_type="write",

    event="salesforce-connector.connect_salesforce",

    effects=['salesforce.provider.connected'],

    data_model=ProviderConnection,
)
async def connect_salesforce(ctx, params: ConnectSalesforceParams) -> ActionResult:
    """Connect a Salesforce organization by saving its Connected App credentials (Consumer Key/Secret + My Domain host) for the OAuth 2.0 Client Credentials Flow, afte..."""
    if not params.client_id or not params.client_secret or not params.my_domain:
        return ActionResult.error("client_id, client_secret, and my_domain are all required.", code="SALESFORCE_VALIDATION_FAILED")
    tok = await sc.get_access_token(ctx, params.client_id, params.client_secret, params.my_domain)
    if not tok.get("ok"):
        return ActionResult.error(tok.get("error", "Could not authenticate with Salesforce."), code=tok.get("error_code", "SALESFORCE_TOKEN_REJECTED"))
    connections = await _load_connections(ctx)
    conn_id = str(uuid.uuid4())
    connections.append({
        "id": conn_id,
        "client_id": params.client_id,
        "client_secret": params.client_secret,
        "my_domain": params.my_domain,
        "label": params.label,
    })
    await _save_connections(ctx, connections)
    entity = ProviderConnection(**_connection_view(connections[-1]))
    return ActionResult.success(entity, f"Connected to Salesforce org at {params.my_domain}.")


@chat.function("list_connections", "List the connected Salesforce organizations.",
    data_model=ProviderConnectionList,
    event="salesforce-connector.list_connections",
)
async def list_connections(ctx, params: NoParams) -> ActionResult:
    """List the connected Salesforce organizations."""
    connections = await _load_connections(ctx)
    items = [ProviderConnection(**_connection_view(c)) for c in connections]
    return ActionResult.success(ProviderConnectionList(items=items), f"{len(items)} connected Salesforce org(s).")


@chat.function(
    "create_record",
    "Create a new record of any Salesforce sObject type -- standard (Account, "
    "Contact, Lead, Opportunity, Case, Task, Event, Campaign, User...) or custom "
    "(any __c object). The REST API is generic across every object, so this one "
    "tool covers all of them.",
    action_type="write",

    event="salesforce-connector.create_record",

    effects=['salesforce.record.created'],

    data_model=RecordResult,
)
async def create_record(ctx, params: CreateRecordParams) -> ActionResult:
    """Create a new record of any Salesforce sObject type -- standard (Account, Contact, Lead, Opportunity, Case, Task, Event, Campaign, User...) or custom (any __c ob..."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.create_record(ctx, access["access_token"], access["instance_url"], params.object_type, params.fields)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Create failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    result = RecordResult(id=body.get("id", ""), object_type=params.object_type, success=body.get("success", True), errors=body.get("errors", []))
    return ActionResult.success(result, f"Created {params.object_type} {result.id}.")


@chat.function(
    "get_record",
    "Read one record of any Salesforce sObject type by Id, optionally limited to specific fields.",
    data_model=RecordDetail,
    event="salesforce-connector.get_record",
)
async def get_record(ctx, params: GetRecordParams) -> ActionResult:
    """Read one record of any Salesforce sObject type by Id, optionally limited to specific fields."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.get_record(ctx, access["access_token"], access["instance_url"], params.object_type, params.record_id, params.fields)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Get record failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(RecordDetail(object_type=params.object_type, record_id=params.record_id, fields=body), f"{params.object_type} {params.record_id}.")


@chat.function(
    "update_record",
    "Update selected fields on an existing record of any Salesforce sObject type. Only given fields change.",
    action_type="write",

    event="salesforce-connector.update_record",

    effects=['salesforce.record.updated'],

    data_model=RecordResult,
)
async def update_record(ctx, params: UpdateRecordParams) -> ActionResult:
    """Update selected fields on an existing record of any Salesforce sObject type."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        await sc.update_record(ctx, access["access_token"], access["instance_url"], params.object_type, params.record_id, params.fields)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Update failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(RecordResult(id=params.record_id, object_type=params.object_type, success=True), f"Updated {params.object_type} {params.record_id}.")


@chat.function(
    "upsert_record",
    "Create or update a record by matching an External ID field instead of the Salesforce record Id -- the standard "
    "way to sync from an external system without tracking Salesforce Ids yourself.",
    action_type="write",

    event="salesforce-connector.upsert_record",

    effects=['salesforce.record.upserted'],

    data_model=RecordResult,
)
async def upsert_record(ctx, params: UpsertRecordParams) -> ActionResult:
    """Create or update a record by matching an External ID field instead of the Salesforce record Id -- the standard way to sync from an external system without track..."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.upsert_record(ctx, access["access_token"], access["instance_url"], params.object_type, params.external_id_field, params.external_id_value, params.fields)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Upsert failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    rid = body.get("id", "") if isinstance(body, dict) else ""
    return ActionResult.success(RecordResult(id=rid, object_type=params.object_type, success=True), f"Upserted {params.object_type} via {params.external_id_field}={params.external_id_value}.")


@chat.function(
    "delete_record",
    "Permanently delete one record of any Salesforce sObject type by Id. Salesforce keeps it recoverable in the Recycle Bin for 15 days.",
    action_type="destructive",

    event="salesforce-connector.delete_record",

    effects=['salesforce.record.deleted'],

    data_model=DeleteResult,
)
async def delete_record(ctx, params: DeleteRecordParams) -> ActionResult:
    """Permanently delete one record of any Salesforce sObject type by Id."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        await sc.delete_record(ctx, access["access_token"], access["instance_url"], params.object_type, params.record_id)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Delete failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(DeleteResult(id=params.record_id, title=params.object_type, ok=True), f"Deleted {params.object_type} {params.record_id}.")


@chat.function(
    "describe_object",
    "Read the field-level metadata (schema) of a Salesforce sObject type -- every field's API name, label, type, "
    "and whether it's required/updateable/a picklist. Use this to discover what fields exist on a standard or custom object.",
    data_model=ObjectDescribe,
    event="salesforce-connector.describe_object",
)
async def describe_object(ctx, params: DescribeObjectParams) -> ActionResult:
    """Read the field-level metadata (schema) of a Salesforce sObject type -- every field's API name, label, type, and whether it's required/updateable/a picklist. Use..."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.describe_sobject(ctx, access["access_token"], access["instance_url"], params.object_type)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Describe failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    fields = [ObjectFieldMeta(name=f.get("name", ""), label=f.get("label", ""), type=f.get("type", ""), required=(not f.get("nillable", True)) and not f.get("defaultedOnCreate", False), updateable=f.get("updateable", False)) for f in body.get("fields", [])]
    return ActionResult.success(ObjectDescribe(object_type=params.object_type, label=body.get("label", ""), fields=fields), f"{params.object_type} has {len(fields)} fields.")


@chat.function(
    "list_objects",
    "List every sObject type available in this org -- standard objects and custom (__c) objects alike.",
    data_model=SObjectSummaryList,
    event="salesforce-connector.list_objects",
)
async def list_objects(ctx, params: ListObjectsParams) -> ActionResult:
    """List every sObject type available in this org -- standard objects and custom (__c) objects alike."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        raw = await sc.list_sobjects(ctx, access["access_token"], access["instance_url"])
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "List objects failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [SObjectSummary(name=o.get("name", ""), label=o.get("label", ""), custom=o.get("custom", False), queryable=o.get("queryable", False), createable=o.get("createable", False)) for o in raw]
    return ActionResult.success(SObjectSummaryList(items=items), f"{len(items)} sObject types.")


@chat.function(
    "run_soql",
    "Run a SOQL query against Salesforce records, e.g. \"SELECT Id, Name FROM Account WHERE Industry = 'Technology'\". "
    "Returns up to 2000 rows plus a next_records_url to page through more with continue_soql.",
    data_model=SoqlResult,
    event="salesforce-connector.run_soql",
)
async def run_soql(ctx, params: RunSoqlParams) -> ActionResult:
    """Run a SOQL query against Salesforce records."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.run_soql(ctx, access["access_token"], access["instance_url"], params.query, params.include_deleted)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "SOQL query failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    rows = [SoqlRow(fields=r) for r in body.get("records", [])]
    result = SoqlResult(total_size=body.get("totalSize", 0), done=body.get("done", True), next_records_url=body.get("nextRecordsUrl", ""), records=rows)
    return ActionResult.success(result, f"{result.total_size} matching record(s).")


@chat.function(
    "continue_soql",
    "Fetch the next page of a large SOQL result set, using the next_records_url returned by run_soql/continue_soql.",
    data_model=SoqlResult,
    event="salesforce-connector.continue_soql",
)
async def continue_soql(ctx, params: ContinueSoqlParams) -> ActionResult:
    """Fetch the next page of a large SOQL result set, using the next_records_url returned by run_soql/continue_soql."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.continue_soql(ctx, access["access_token"], access["instance_url"], params.next_records_url)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Continue SOQL failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    rows = [SoqlRow(fields=r) for r in body.get("records", [])]
    result = SoqlResult(total_size=body.get("totalSize", 0), done=body.get("done", True), next_records_url=body.get("nextRecordsUrl", ""), records=rows)
    return ActionResult.success(result, f"{len(rows)} more record(s).")


@chat.function(
    "run_sosl",
    "Run a SOSL full-text search across multiple sObject types at once, e.g. \"FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name), Contact(Id, Name)\".",
    data_model=SoslResult,
    event="salesforce-connector.run_sosl",
)
async def run_sosl(ctx, params: RunSoslParams) -> ActionResult:
    """Run a SOSL full-text search across multiple sObject types at once."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.run_sosl(ctx, access["access_token"], access["instance_url"], params.search)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "SOSL search failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    rows = [SoqlRow(fields=r) for r in body.get("searchRecords", [])]
    return ActionResult.success(SoslResult(records=rows), f"{len(rows)} matching record(s) across objects.")


@chat.function(
    "run_composite",
    "Run up to 25 different sObject operations in ONE atomic-or-not HTTP request -- Salesforce's own native batching, "
    "not a wrapper. Each sub-request has a reference_id so later sub-requests in the same call can reference its result.",
    action_type="write",

    event="salesforce-connector.run_composite",

    effects=['salesforce.composite.executed'],

    data_model=CompositeResult,
)
async def run_composite(ctx, params: RunCompositeParams) -> ActionResult:
    """Run up to 25 different sObject operations in ONE atomic-or-not HTTP request -- Salesforce's own native batching, not a wrapper."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    sub_requests = [{"method": r.method, "url": r.url, "referenceId": r.reference_id, "body": r.body} for r in params.requests]
    try:
        body = await sc.run_composite(ctx, access["access_token"], access["instance_url"], sub_requests, params.all_or_none)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Composite request failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    results = [CompositeSubResult(reference_id=r.get("referenceId", ""), http_status=r.get("httpStatusCode", 0), body=r.get("body") or {}) for r in body.get("compositeResponse", [])]
    return ActionResult.success(CompositeResult(results=results), f"Ran {len(results)} composite sub-request(s).")


@chat.function(
    "create_bulk_job",
    "Start a Bulk API 2.0 job for mass insert/update/upsert/delete/hardDelete/query/queryAll -- for volumes too large "
    "for single record calls or run_soql's 2000-row page (Bulk API 2.0 handles up to 150 million records per job). "
    "For insert/update/upsert/delete, pass csv_data (header row + data rows); for query/queryAll, pass a SOQL query instead.",
    action_type="write",

    event="salesforce-connector.create_bulk_job",

    effects=['salesforce.bulk_job.created'],

    data_model=BulkJob,
)
async def create_bulk_job(ctx, params: CreateBulkJobParams) -> ActionResult:
    """Start a Bulk API 2.0 job for mass insert/update/upsert/delete/hardDelete/query/queryAll -- for volumes too large for single record calls or run_soql's 2000-row..."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.create_bulk_job(
            ctx, access["access_token"], access["instance_url"], params.object_type, params.operation,
            params.external_id_field, params.csv_data, params.query,
        )
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Create bulk job failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    job = BulkJob(
        job_id=body.get("id", ""), object_type=body.get("object", params.object_type), operation=body.get("operation", params.operation),
        state=body.get("state", ""), created_date=body.get("createdDate", ""),
        records_processed=body.get("numberRecordsProcessed", 0), records_failed=body.get("numberRecordsFailed", 0),
    )
    return ActionResult.success(job, f"Bulk job {job.job_id} created ({job.state}).")


@chat.function(
    "get_bulk_job",
    "Check the status of a Bulk API 2.0 job -- state, records processed, records failed.",
    data_model=BulkJob,
    event="salesforce-connector.get_bulk_job",
)
async def get_bulk_job(ctx, params: GetBulkJobParams) -> ActionResult:
    """Check the status of a Bulk API 2.0 job -- state, records processed, records failed."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.get_bulk_job(ctx, access["access_token"], access["instance_url"], params.job_id, params.query_job)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Get bulk job failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    job = BulkJob(
        job_id=body.get("id", params.job_id), object_type=body.get("object", ""), operation=body.get("operation", ""),
        state=body.get("state", ""), created_date=body.get("createdDate", ""),
        records_processed=body.get("numberRecordsProcessed", 0), records_failed=body.get("numberRecordsFailed", 0),
    )
    return ActionResult.success(job, f"Bulk job {job.job_id}: {job.state}.")


@chat.function(
    "list_bulk_jobs",
    "List recent Bulk API 2.0 jobs in this org, most recent first.",
    data_model=BulkJobList,
    event="salesforce-connector.list_bulk_jobs",
)
async def list_bulk_jobs(ctx, params: ListBulkJobsParams) -> ActionResult:
    """List recent Bulk API 2.0 jobs in this org, most recent first."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        raw = await sc.list_bulk_jobs(ctx, access["access_token"], access["instance_url"], params.query_job)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "List bulk jobs failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [BulkJob(
        job_id=j.get("id", ""), object_type=j.get("object", ""), operation=j.get("operation", ""),
        state=j.get("state", ""), created_date=j.get("createdDate", ""),
        records_processed=j.get("numberRecordsProcessed", 0), records_failed=j.get("numberRecordsFailed", 0),
    ) for j in raw]
    return ActionResult.success(BulkJobList(items=items), f"{len(items)} bulk job(s).")


@chat.function(
    "get_bulk_job_results",
    "Read the CSV results of a completed Bulk API 2.0 job -- successful records, failed records, unprocessed "
    "records, or the query rows for a query/queryAll job.",
    data_model=BulkJobResults,
    event="salesforce-connector.get_bulk_job_results",
)
async def get_bulk_job_results(ctx, params: BulkJobResultsParams) -> ActionResult:
    """Read the CSV results of a completed Bulk API 2.0 job -- successful records, failed records, unprocessed records, or the query rows for a query/queryAll job."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        csv_text = await sc.get_bulk_job_results(ctx, access["access_token"], access["instance_url"], params.job_id, params.result_type)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Get bulk job results failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(BulkJobResults(job_id=params.job_id, result_type=params.result_type, csv_data=csv_text), f"Fetched {params.result_type} for job {params.job_id}.")


@chat.function(
    "abort_bulk_job",
    "Abort a Bulk API 2.0 job that is still open or in progress.",
    action_type="write",

    event="salesforce-connector.abort_bulk_job",

    effects=['salesforce.bulk_job.aborted'],

    data_model=DeleteResult,
)
async def abort_bulk_job(ctx, params: AbortBulkJobParams) -> ActionResult:
    """Abort a Bulk API 2.0 job that is still open or in progress."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        await sc.abort_bulk_job(ctx, access["access_token"], access["instance_url"], params.job_id, params.query_job)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Abort bulk job failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(DeleteResult(id=params.job_id, title="aborted", ok=True), f"Aborted bulk job {params.job_id}.")


@chat.function(
    "convert_lead",
    "Convert a Lead into an Account, Contact, and (optionally) an Opportunity -- Salesforce's own dedicated conversion "
    "process, not a plain field update. Requires the org's actual 'Converted' Lead Status picklist value.",
    action_type="write",

    event="salesforce-connector.convert_lead",

    effects=['salesforce.lead.converted'],

    data_model=LeadConvertResult,
)
async def convert_lead(ctx, params: ConvertLeadParams) -> ActionResult:
    """Convert a Lead into an Account, Contact, and (optionally) an Opportunity -- Salesforce's own dedicated conversion process, not a plain field update. Requires th..."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.convert_lead(
            ctx, access["access_token"], access["instance_url"], params.lead_id, params.converted_status,
            params.create_opportunity, params.opportunity_name, params.account_id, params.contact_id,
        )
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Convert lead failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    result = LeadConvertResult(
        lead_id=params.lead_id, account_id=body.get("accountId", ""), contact_id=body.get("contactId", ""),
        opportunity_id=body.get("opportunityId", ""), success=body.get("success", True),
    )
    return ActionResult.success(result, f"Converted Lead {params.lead_id}.")


@chat.function(
    "post_chatter_feed",
    "Post a new Chatter feed item (a comment/update) on any record -- the collaboration layer Salesforce users see on a record's page.",
    action_type="write",

    event="salesforce-connector.post_chatter_feed",

    effects=['salesforce.chatter.posted'],

    data_model=FeedPostResult,
)
async def post_chatter_feed(ctx, params: PostChatterFeedParams) -> ActionResult:
    """Post a new Chatter feed item (a comment/update) on any record -- the collaboration layer Salesforce users see on a record's page."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.post_chatter_feed(ctx, access["access_token"], access["instance_url"], params.record_id, params.text)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Post to Chatter failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(FeedPostResult(feed_item_id=body.get("id", ""), success=True), f"Posted to the Chatter feed of {params.record_id}.")


@chat.function(
    "list_chatter_feed",
    "Read the Chatter feed (comments/updates) posted on one record.",
    data_model=ChatterFeedList,
    event="salesforce-connector.list_chatter_feed",
)
async def list_chatter_feed(ctx, params: ListChatterFeedParams) -> ActionResult:
    """Read the Chatter feed (comments/updates) posted on one record."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.list_chatter_feed(ctx, access["access_token"], access["instance_url"], params.record_id)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "List Chatter feed failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [FeedItem(id=el.get("id", ""), body=(el.get("body", {}) or {}).get("text", ""), actor_name=(el.get("actor", {}) or {}).get("displayName", ""), created_date=el.get("createdDate", "")) for el in body.get("elements", [])]
    return ActionResult.success(ChatterFeedList(items=items), f"{len(items)} feed item(s).")


@chat.function(
    "comment_on_feed",
    "Add a comment to an existing Chatter feed item.",
    action_type="write",

    event="salesforce-connector.comment_on_feed",

    effects=['salesforce.chatter.commented'],

    data_model=DeleteResult,
)
async def comment_on_feed(ctx, params: CommentOnFeedParams) -> ActionResult:
    """Add a comment to an existing Chatter feed item."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        await sc.comment_on_feed(ctx, access["access_token"], access["instance_url"], params.feed_item_id, params.text)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Comment on feed failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(DeleteResult(id=params.feed_item_id, title="commented", ok=True), "Comment posted.")


@chat.function(
    "list_record_files",
    "List files (ContentDocuments) attached to a record via the Files related list.",
    data_model=RecordFileList,
    event="salesforce-connector.list_record_files",
)
async def list_record_files(ctx, params: ListRecordFilesParams) -> ActionResult:
    """List files (ContentDocuments) attached to a record via the Files related list."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        raw = await sc.list_record_files(ctx, access["access_token"], access["instance_url"], params.record_id)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "List record files failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [RecordFile(content_document_id=r.get("ContentDocumentId", ""), title=(r.get("ContentDocument", {}) or {}).get("Title", ""), file_type=(r.get("ContentDocument", {}) or {}).get("FileType", "")) for r in raw]
    return ActionResult.success(RecordFileList(items=items), f"{len(items)} file(s) attached.")


@chat.function(
    "upload_file",
    "Upload a file and attach it to a record -- creates a ContentVersion and links it via ContentDocumentLink.",
    action_type="write",

    event="salesforce-connector.upload_file",

    effects=['salesforce.file.uploaded'],

    data_model=FileUploadResult,
)
async def upload_file(ctx, params: UploadFileParams) -> ActionResult:
    """Upload a file and attach it to a record -- creates a ContentVersion and links it via ContentDocumentLink."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.upload_file(ctx, access["access_token"], access["instance_url"], params.record_id, params.title, params.base64_data, params.path_on_client)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Upload file failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    return ActionResult.success(FileUploadResult(content_document_id=body.get("id", ""), success=True), f"Uploaded {params.title} and attached it to {params.record_id}.")


@chat.function(
    "list_reports",
    "List Salesforce reports available to run.",
    data_model=ReportList,
    event="salesforce-connector.list_reports",
)
async def list_reports(ctx, params: ListReportsParams) -> ActionResult:
    """List Salesforce reports available to run."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        rows = await sc.list_reports(ctx, access["access_token"], access["instance_url"])
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "List reports failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [ReportSummary(id=r.get("id", ""), name=r.get("name", ""), folder_name=(r.get("reportFolder") or {}).get("name", "") if isinstance(r.get("reportFolder"), dict) else "") for r in rows]
    return ActionResult.success(ReportList(items=items), f"{len(items)} report(s).")


@chat.function(
    "run_report",
    "Run a Salesforce report by Id and return its summarized result rows.",
    data_model=ReportResult,
    event="salesforce-connector.run_report",
)
async def run_report(ctx, params: RunReportParams) -> ActionResult:
    """Run a Salesforce report by Id and return its summarized result rows."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.run_report(ctx, access["access_token"], access["instance_url"], params.report_id)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Run report failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    fact_map = ((body.get("factMap") or {}).get("T!T") or {})
    rows = [ReportRow(label=(r.get("dataCells") or [{}])[0].get("label", ""), values={}) for r in (fact_map.get("rows") or [])]
    grand_total = str((fact_map.get("aggregates") or [{}])[0].get("label", "")) if fact_map.get("aggregates") else ""
    result = ReportResult(report_id=params.report_id, report_name=(body.get("attributes") or {}).get("reportName", ""), rows=rows, grand_total=grand_total)
    return ActionResult.success(result, f"Report returned {len(rows)} row(s).")


@chat.function(
    "list_dashboards",
    "List Salesforce dashboards.",
    data_model=DashboardList,
    event="salesforce-connector.list_dashboards",
)
async def list_dashboards(ctx, params: ListDashboardsParams) -> ActionResult:
    """List Salesforce dashboards."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        rows = await sc.list_dashboards(ctx, access["access_token"], access["instance_url"])
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "List dashboards failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [DashboardSummary(id=d.get("id", ""), title=d.get("title", ""), folder_name=(d.get("folder") or {}).get("name", "") if isinstance(d.get("folder"), dict) else "") for d in rows]
    return ActionResult.success(DashboardList(items=items), f"{len(items)} dashboard(s).")


@chat.function(
    "get_dashboard",
    "Read one dashboard's components/values by Id.",
    data_model=DashboardDetail,
    event="salesforce-connector.get_dashboard",
)
async def get_dashboard(ctx, params: GetDashboardParams) -> ActionResult:
    """Read one dashboard's components/values by Id."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.get_dashboard(ctx, access["access_token"], access["instance_url"], params.dashboard_id)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Get dashboard failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    comps = [DashboardComponent(title=c.get("title", ""), value=str((c.get("data") or {}))) for c in ((body.get("status") or {}).get("componentData") or [])] if isinstance(body.get("status"), dict) else []
    result = DashboardDetail(dashboard_id=params.dashboard_id, title=(body.get("attributes") or {}).get("title", ""), components=comps)
    return ActionResult.success(result, f"Dashboard has {len(comps)} component(s).")


@chat.function(
    "submit_for_approval",
    "Submit a record into its assigned Approval Process (e.g. a big discount Opportunity, or an Expense Report over a threshold).",
    action_type="write",

    event="salesforce-connector.submit_for_approval",

    effects=['salesforce.approval.submitted'],

    data_model=ApprovalActionResult,
)
async def submit_for_approval(ctx, params: SubmitForApprovalParams) -> ActionResult:
    """Submit a record into its assigned Approval Process (e.g."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.submit_for_approval(ctx, access["access_token"], access["instance_url"], params.record_id, params.comments)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Submit for approval failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    row = (body[0] if isinstance(body, list) and body else body) if isinstance(body, (list, dict)) else {}
    result = ApprovalActionResult(record_id=params.record_id, success=row.get("success", True) if isinstance(row, dict) else True, instance_id=row.get("instanceId", "") if isinstance(row, dict) else "")
    return ActionResult.success(result, f"Submitted {params.record_id} for approval.")


@chat.function(
    "process_approval",
    "Approve, reject, or remove a pending approval work item -- acting on behalf of the assigned approver.",
    action_type="write",

    event="salesforce-connector.process_approval",

    effects=['salesforce.approval.processed'],

    data_model=ApprovalActionResult,
)
async def process_approval(ctx, params: ProcessApprovalParams) -> ActionResult:
    """Approve, reject, or remove a pending approval work item -- acting on behalf of the assigned approver."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.process_approval(ctx, access["access_token"], access["instance_url"], params.work_item_id, params.action, params.comments)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Process approval failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    row = (body[0] if isinstance(body, list) and body else body) if isinstance(body, (list, dict)) else {}
    result = ApprovalActionResult(record_id=params.work_item_id, success=row.get("success", True) if isinstance(row, dict) else True, instance_id=row.get("instanceId", "") if isinstance(row, dict) else "")
    return ActionResult.success(result, f"{params.action} applied to work item {params.work_item_id}.")


@chat.function(
    "list_approval_work_items",
    "List pending approval work items across the org (records currently awaiting a decision).",
    data_model=ApprovalWorkItemList,
    event="salesforce-connector.list_approval_work_items",
)
async def list_approval_work_items(ctx, params: ListApprovalsParams) -> ActionResult:
    """List pending approval work items across the org (records currently awaiting a decision)."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        rows = await sc.list_approval_work_items(ctx, access["access_token"], access["instance_url"])
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "List approval work items failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [ApprovalWorkItem(id=r.get("Id", ""), actor_id=r.get("ActorId", ""), process_instance_id=r.get("ProcessInstanceId", "")) for r in rows]
    return ActionResult.success(ApprovalWorkItemList(items=items), f"{len(items)} pending approval(s).")


@chat.function(
    "send_email",
    "Send a transactional email through Salesforce (using the org's own send-email capability), optionally logging it against a related record.",
    action_type="write",

    event="salesforce-connector.send_email",

    effects=['salesforce.email.sent'],

    data_model=SendEmailResult,
)
async def send_email(ctx, params: SendEmailParams) -> ActionResult:
    """Send a transactional email through Salesforce (using the org's own send-email capability), optionally logging it against a related record."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.send_email(ctx, access["access_token"], access["instance_url"], params.to_addresses, params.subject, params.body, params.related_record_id, params.save_as_activity)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Send email failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    row = (body[0] if isinstance(body, list) and body else body) if isinstance(body, (list, dict)) else {}
    success = row.get("success", True) if isinstance(row, dict) else True
    errors = row.get("errors", []) if isinstance(row, dict) else []
    return ActionResult.success(SendEmailResult(success=success, errors=errors), f"Email sent to {len(params.to_addresses)} recipient(s).")


@chat.function(
    "get_org_limits",
    "Read the org's current API/storage/feature limits and how much of each has been used -- e.g. daily API request budget remaining.",
    data_model=OrgLimitsResult,
    event="salesforce-connector.get_org_limits",
)
async def get_org_limits(ctx, params: GetOrgLimitsParams) -> ActionResult:
    """Read the org's current API/storage/feature limits and how much of each has been used -- e.g."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.get_org_limits(ctx, access["access_token"], access["instance_url"])
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Get org limits failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    limits = [OrgLimit(name=name, max=v.get("Max", 0), remaining=v.get("Remaining", 0)) for name, v in (body.items() if isinstance(body, dict) else [])]
    return ActionResult.success(OrgLimitsResult(limits=limits), f"{len(limits)} limit(s) read.")


@chat.function(
    "publish_platform_event",
    "Publish a custom Platform Event -- Salesforce's own pub/sub messaging mechanism for notifying other systems/Flows/Apex triggers in near real time.",
    action_type="write",

    event="salesforce-connector.publish_platform_event",

    effects=['salesforce.platform_event.published'],

    data_model=PlatformEventResult,
)
async def publish_platform_event(ctx, params: PublishPlatformEventParams) -> ActionResult:
    """Publish a custom Platform Event -- Salesforce's own pub/sub messaging mechanism for notifying other systems/Flows/Apex triggers in near real time."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.publish_platform_event(ctx, access["access_token"], access["instance_url"], params.event_type, params.fields)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Publish platform event failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    result = PlatformEventResult(event_type=params.event_type, success=body.get("success", True), replay_id=str(body.get("id", "")))
    return ActionResult.success(result, f"Published {params.event_type} event.")


@chat.function(
    "bulk_update_records",
    "Update the same fields on up to 200 explicit records of one object type in a single call -- for routine batch fixes too small for a Bulk API 2.0 job.",
    action_type="write",

    event="salesforce-connector.bulk_update_records",

    effects=['salesforce.record.updated'],

    data_model=BulkRecordResult,
)
async def bulk_update_records(ctx, params: BulkRecordIdsParams) -> ActionResult:
    """Update the same fields on up to 200 explicit records of one object type in a single call -- for routine batch fixes too small for a Bulk API 2.0 job."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    fields = getattr(params, "fields", None) or {}
    try:
        rows = await sc.bulk_update_records(ctx, access["access_token"], access["instance_url"], params.object_type, params.record_ids, fields)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Bulk update failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [BulkRecordResultItem(record_id=r.get("record_id", ""), success=r.get("ok", False), error=r.get("error", "") or "") for r in rows]
    ok_count = sum(1 for i in items if i.success)
    return ActionResult.success(BulkRecordResult(results=items), f"{ok_count}/{len(items)} record(s) updated.")


@chat.function(
    "bulk_delete_records",
    "Delete up to 200 explicit records of one object type in a single call -- for routine batch cleanup too small for a Bulk API 2.0 job.",
    action_type="destructive",

    event="salesforce-connector.bulk_delete_records",

    effects=['salesforce.record.deleted'],

    data_model=BulkRecordResult,
)
async def bulk_delete_records(ctx, params: BulkRecordIdsParams) -> ActionResult:
    """Delete up to 200 explicit records of one object type in a single call -- for routine batch cleanup too small for a Bulk API 2.0 job."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        rows = await sc.bulk_delete_records(ctx, access["access_token"], access["instance_url"], params.object_type, params.record_ids)
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Bulk delete failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    items = [BulkRecordResultItem(record_id=r.get("record_id", ""), success=r.get("ok", False), error=r.get("error", "") or "") for r in rows]
    ok_count = sum(1 for i in items if i.success)
    return ActionResult.success(BulkRecordResult(results=items), f"{ok_count}/{len(items)} record(s) deleted.")


@chat.function(
    "audit_org",
    "Build an aggregated org health snapshot: API usage limits plus headline record counts (Account/Contact/Lead/Opportunity/Case) -- same value-add shape as MuleSoft/UiPath/Automation Anywhere/Blue Prism's estate audits.",
    data_model=OrgAuditReport,
    event="salesforce-connector.audit_org",
)
async def audit_org(ctx, params: AuditOrgParams) -> ActionResult:
    """Build an aggregated org health snapshot: API usage limits plus headline record counts (Account/Contact/Lead/Opportunity/Case) -- same value-add shape as MuleSof..."""
    access = await _get_access(ctx, params.connection_id)
    if not access.get("ok"):
        return ActionResult.error(access.get("error", "Not connected."), code=access.get("error_code", "SALESFORCE_NOT_CONNECTED"))
    try:
        body = await sc.audit_org(ctx, access["access_token"], access["instance_url"])
    except sc.ClientFail as e:
        return ActionResult.error(e.payload.get("error", "Audit org failed."), retryable=e.payload.get("retryable", False), code=e.payload.get("error_code", "SALESFORCE_ERROR"))
    rows = [OrgAuditRow(check=r.get("check", ""), status=r.get("status", ""), detail=r.get("detail", "")) for r in body.get("rows", [])] if isinstance(body, dict) else []
    return ActionResult.success(OrgAuditReport(rows=rows), f"Org audit complete: {len(rows)} check(s).")


@chat.function(
    "disconnect_salesforce",
    "Disconnect one Salesforce organization. Nothing in Salesforce itself is changed; only the saved credentials here are deleted.",
    action_type="write",

    event="salesforce-connector.disconnect_salesforce",

    effects=['salesforce.provider.disconnected'],

    data_model=DeleteResult,
)
async def disconnect_salesforce(ctx, params: DisconnectSalesforceParams) -> ActionResult:
    """Disconnect one Salesforce organization."""
    connections = await _load_connections(ctx)
    remaining = [c for c in connections if c.get("id") != params.connection_id]
    if len(remaining) == len(connections):
        return ActionResult.error("No such connection.", code="SALESFORCE_NOT_FOUND")
    await _save_connections(ctx, remaining)
    return ActionResult.success(DeleteResult(ok=True, detail="Disconnected."), "Disconnected from Salesforce.")
