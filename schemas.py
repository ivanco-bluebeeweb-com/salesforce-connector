"""Pydantic params models + SDL entity contracts for Salesforce Connector.

All params models are module-scope (V17 federal invariant, same rule as
MuleSoft Connector / Power Automate Connector / Make.com Connector's
schemas.py).
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from imperal_sdk import sdl


class NoParams(BaseModel):
    """Explicit empty params model -- V17 disallows untyped handlers."""
    pass


# ─────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────


class ConnectSalesforceParams(BaseModel):
    client_id: str = Field(
        "",
        description="Consumer Key of the Salesforce Connected App (Setup > App Manager > your app > View > Consumer Key).",
    )
    client_secret: str = Field(
        "",
        description="Consumer Secret of the Salesforce Connected App.",
    )
    my_domain: str = Field(
        "",
        description="Your org's My Domain hostname, e.g. 'mycompany.my.salesforce.com' (Setup > My Domain). Do not include https://.",
    )
    label: str = Field("", description="Optional friendly name for this organization connection.")


class ProviderConnection(sdl.Entity):
    id: str = ""
    title: str = ""
    connected: bool = False
    detail: str = ""
    my_domain: str = ""


class ProviderConnectionList(sdl.Entity):
    items: list[ProviderConnection] = Field(default_factory=list)


class DisconnectSalesforceParams(BaseModel):
    connection_id: str = Field(..., description="Connection id from list_connections.")


class DeleteResult(sdl.Entity):
    ok: bool = True
    detail: str = ""


# ─────────────────────────────────────────────────────────────────────────
# Generic sObject CRUD -- the REST API is itself generic
# (/services/data/vNN/sobjects/{ObjectType}/...), so ONE set of tools
# covers every standard object (Account, Contact, Lead, Opportunity,
# Case, Task, Event, Campaign, User...) AND every custom object (__c
# suffix), rather than hand-rolling 20+ near-identical per-object tools.
# ─────────────────────────────────────────────────────────────────────────


class CreateRecordParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name, e.g. 'Account', 'Contact', 'Lead', 'MyCustom__c'.")
    fields: dict = Field(default_factory=dict, description="Field API name -> value pairs to set on the new record.")


class RecordResult(sdl.Entity):
    id: str = ""
    object_type: str = ""
    success: bool = True
    errors: list[str] = Field(default_factory=list)


class GetRecordParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name, e.g. 'Account'.")
    record_id: str = Field(..., description="18-character Salesforce record Id.")
    fields: str = Field("", description="Optional comma-separated list of field API names to return; empty returns all fields.")


class RecordDetail(sdl.Entity):
    object_type: str = ""
    record_id: str = ""
    fields: dict = Field(default_factory=dict)


class UpdateRecordParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name, e.g. 'Opportunity'.")
    record_id: str = Field(..., description="18-character Salesforce record Id.")
    fields: dict = Field(..., description="Field API name -> new value pairs to update.")


class UpsertRecordParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name, e.g. 'Contact'.")
    external_id_field: str = Field(..., description="API name of the External ID field used to match an existing record.")
    external_id_value: str = Field(..., description="Value of the external id to match/create by.")
    fields: dict = Field(default_factory=dict, description="Field API name -> value pairs to set.")


class DeleteRecordParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name, e.g. 'Task'.")
    record_id: str = Field(..., description="18-character Salesforce record Id.")


class DescribeObjectParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name to describe, e.g. 'Account'.")


class ObjectFieldMeta(sdl.Entity):
    name: str = ""
    label: str = ""
    type: str = ""
    required: bool = False
    updateable: bool = True
    picklist_values: list[str] = Field(default_factory=list)


class ObjectDescribe(sdl.Entity):
    object_type: str = ""
    label: str = ""
    custom: bool = False
    createable: bool = True
    updateable: bool = True
    deletable: bool = True
    fields: list[ObjectFieldMeta] = Field(default_factory=list)


class ListObjectsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")


class SObjectSummary(sdl.Entity):
    name: str = ""
    label: str = ""
    custom: bool = False
    queryable: bool = True


class SObjectSummaryList(sdl.Entity):
    items: list[SObjectSummary] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# SOQL / SOSL query + Composite/Batch
# ─────────────────────────────────────────────────────────────────────────


class RunSoqlParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    query: str = Field(..., description="A SOQL query, e.g. \"SELECT Id, Name FROM Account WHERE Industry = 'Technology'\".")
    include_deleted: bool = Field(False, description="Use queryAll to also include soft-deleted/archived records.")


class SoqlRow(sdl.Entity):
    fields: dict = Field(default_factory=dict)


class SoqlResult(sdl.Entity):
    total_size: int = 0
    done: bool = True
    next_records_url: str = ""
    records: list[SoqlRow] = Field(default_factory=list)


class ContinueSoqlParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    next_records_url: str = Field(..., description="The nextRecordsUrl returned by a previous run_soql/continue_soql call, for paginating large result sets.")


class RunSoslParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    search: str = Field(..., description="A SOSL search string, e.g. \"FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name), Contact(Id, Name)\".")


class SoslResult(sdl.Entity):
    records: list[SoqlRow] = Field(default_factory=list)


class CompositeSubRequest(BaseModel):
    method: str = Field(..., description="HTTP method for this sub-request: GET, POST, PATCH, or DELETE.")
    url: str = Field(..., description="Relative REST URL for this sub-request, e.g. '/services/data/v62.0/sobjects/Account'.")
    reference_id: str = Field(..., description="A label for this sub-request so later sub-requests can reference its result.")
    body: dict = Field(default_factory=dict, description="Request body for POST/PATCH sub-requests.")


class RunCompositeParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    requests: list[CompositeSubRequest] = Field(..., description="Up to 25 sub-requests to run as one atomic-or-not composite call.")
    all_or_none: bool = Field(False, description="If true, all sub-requests roll back together if any one fails.")


class CompositeSubResult(sdl.Entity):
    reference_id: str = ""
    http_status: int = 0
    body: dict = Field(default_factory=dict)


class CompositeResult(sdl.Entity):
    results: list[CompositeSubResult] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Bulk API 2.0 -- async job-based mass insert/update/upsert/delete/query
# ─────────────────────────────────────────────────────────────────────────


class CreateBulkJobParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name the job operates on, e.g. 'Lead'.")
    operation: str = Field(..., description="One of: insert, update, upsert, delete, hardDelete, query, queryAll.")
    external_id_field: str = Field("", description="Required only for operation='upsert' -- the External ID field API name.")
    csv_data: str = Field("", description="CSV content (header row + data rows) for insert/update/upsert/delete jobs. Not used for query/queryAll (use 'query' instead).")
    query: str = Field("", description="SOQL query text, required only for operation='query'/'queryAll'.")


class BulkJob(sdl.Entity):
    job_id: str = ""
    object_type: str = ""
    operation: str = ""
    state: str = ""
    created_date: str = ""
    records_processed: int = 0
    records_failed: int = 0


class BulkJobList(sdl.Entity):
    items: list[BulkJob] = Field(default_factory=list)


class GetBulkJobParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    job_id: str = Field(..., description="Bulk API 2.0 job id from create_bulk_job.")
    query_job: bool = Field(False, description="True if this is a query/queryAll job (different endpoint family than ingest jobs).")


class ListBulkJobsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    job_type: str = Field("ingest", description="'ingest' for insert/update/upsert/delete jobs, or 'query' for query/queryAll jobs.")


class BulkJobResultsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    job_id: str = Field(..., description="Bulk API 2.0 job id.")
    result_type: str = Field("successfulResults", description="One of: successfulResults, failedResults, unprocessedrecords (ingest jobs), or 'results' for query jobs.")


class BulkJobResults(sdl.Entity):
    job_id: str = ""
    result_type: str = ""
    csv_data: str = ""


class AbortBulkJobParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    job_id: str = Field(..., description="Bulk API 2.0 job id to abort.")
    query_job: bool = Field(False, description="True if this is a query/queryAll job.")


# ─────────────────────────────────────────────────────────────────────────
# CRM convenience wrappers -- thin named helpers over generic sObject CRUD
# for the highest-traffic objects, PLUS the one action generic CRUD cannot
# express: converting a Lead (a dedicated Salesforce process/endpoint, not
# a plain field update).
# ─────────────────────────────────────────────────────────────────────────


class ConvertLeadParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    lead_id: str = Field(..., description="Id of the Lead to convert.")
    converted_status: str = Field(..., description="The Lead Status picklist value that represents 'Converted' in this org.")
    create_opportunity: bool = Field(True, description="Whether to also create an Opportunity from this lead.")
    opportunity_name: str = Field("", description="Name for the new Opportunity, if create_opportunity is true.")
    account_id: str = Field("", description="Existing Account Id to attach this conversion to, instead of creating a new Account.")
    contact_id: str = Field("", description="Existing Contact Id to attach this conversion to, instead of creating a new Contact.")
    do_not_create_opportunity: bool = Field(False, description="Deprecated alias kept for clarity -- prefer create_opportunity=false.")


class LeadConvertResult(sdl.Entity):
    lead_id: str = ""
    account_id: str = ""
    contact_id: str = ""
    opportunity_id: str = ""
    success: bool = True


# ─────────────────────────────────────────────────────────────────────────
# Connect REST API -- Chatter (feed posts/comments) + Files
# ─────────────────────────────────────────────────────────────────────────


class PostChatterFeedParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    subject_id: str = Field(..., description="Id of the record (or 'me') this feed post is attached to.")
    text: str = Field(..., description="Plain text body of the feed post.")


class FeedPostResult(sdl.Entity):
    id: str = ""
    body: str = ""
    created_date: str = ""


class ListChatterFeedParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    subject_id: str = Field(..., description="Id of the record (or 'me') to read the feed for.")


class FeedItem(sdl.Entity):
    id: str = ""
    actor_name: str = ""
    body: str = ""
    created_date: str = ""
    comment_count: int = 0
    like_count: int = 0


class ChatterFeedList(sdl.Entity):
    items: list[FeedItem] = Field(default_factory=list)


class CommentOnFeedParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    feed_item_id: str = Field(..., description="Id of the feed item (post) to comment on.")
    text: str = Field(..., description="Plain text body of the comment.")


class UploadFileParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    title: str = Field(..., description="File title/name shown in Salesforce Files.")
    base64_data: str = Field(..., description="Raw file content, base64-encoded.")
    path_on_client: str = Field(..., description="Filename with extension, e.g. 'quote.pdf' -- determines the file type icon.")
    record_id: str = Field("", description="Optional record Id to attach this file to immediately after upload.")


class FileUploadResult(sdl.Entity):
    content_document_id: str = ""
    content_version_id: str = ""
    title: str = ""


class ListRecordFilesParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    record_id: str = Field(..., description="Id of the record whose attached files to list.")


class RecordFile(sdl.Entity):
    content_document_id: str = ""
    title: str = ""
    file_type: str = ""
    content_size: int = 0


class RecordFileList(sdl.Entity):
    items: list[RecordFile] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Reports & Dashboards API (read-only -- reporting logic lives in
# Salesforce itself, this connector surfaces it, not reimplements it)
# ─────────────────────────────────────────────────────────────────────────


class ListReportsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")


class ReportSummary(sdl.Entity):
    id: str = ""
    name: str = ""
    folder_name: str = ""
    format: str = ""


class ReportList(sdl.Entity):
    items: list[ReportSummary] = Field(default_factory=list)


class RunReportParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    report_id: str = Field(..., description="Id of the report to run, from list_reports.")


class ReportRow(sdl.Entity):
    label: str = ""
    values: dict = Field(default_factory=dict)


class ReportResult(sdl.Entity):
    report_id: str = ""
    report_name: str = ""
    rows: list[ReportRow] = Field(default_factory=list)
    grand_total: str = ""


class ListDashboardsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")


class DashboardSummary(sdl.Entity):
    id: str = ""
    title: str = ""
    folder_name: str = ""


class DashboardList(sdl.Entity):
    items: list[DashboardSummary] = Field(default_factory=list)


class GetDashboardParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    dashboard_id: str = Field(..., description="Id of the dashboard to read, from list_dashboards.")


class DashboardComponent(sdl.Entity):
    title: str = ""
    value: str = ""


class DashboardDetail(sdl.Entity):
    dashboard_id: str = ""
    title: str = ""
    components: list[DashboardComponent] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Approval Process API
# ─────────────────────────────────────────────────────────────────────────


class SubmitForApprovalParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    record_id: str = Field(..., description="Id of the record to submit for approval.")
    comments: str = Field("", description="Optional comments to attach to the approval submission.")
    process_name: str = Field("", description="Optional specific approval process API name; omitted lets Salesforce pick the applicable one.")


class ProcessApprovalParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    work_item_id: str = Field(..., description="Id of the ProcessInstanceWorkitem to act on.")
    action: str = Field(..., description="'Approve' or 'Reject'.")
    comments: str = Field("", description="Optional comments to attach to the decision.")


class ApprovalActionResult(sdl.Entity):
    record_id: str = ""
    instance_id: str = ""
    status: str = ""
    success: bool = True


class ListApprovalsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    record_id: str = Field("", description="Optional record Id to filter pending approval work items to just that record.")


class ApprovalWorkItem(sdl.Entity):
    work_item_id: str = ""
    record_id: str = ""
    record_name: str = ""
    process_name: str = ""
    assigned_to: str = ""


class ApprovalWorkItemList(sdl.Entity):
    items: list[ApprovalWorkItem] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Transactional email (sObjects Email API) + org limits + Platform Events
# ─────────────────────────────────────────────────────────────────────────


class SendEmailParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    to_addresses: list[str] = Field(..., description="Recipient email addresses.")
    subject: str = Field(..., description="Email subject line.")
    body: str = Field(..., description="Plain-text email body.")
    related_record_id: str = Field("", description="Optional record Id (e.g. a Contact or Case) to log this email against.")
    save_as_activity: bool = Field(True, description="Whether to log this send as an Activity/Task on the related record.")


class SendEmailResult(sdl.Entity):
    success: bool = True
    errors: list[str] = Field(default_factory=list)


class GetOrgLimitsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")


class OrgLimit(sdl.Entity):
    name: str = ""
    max: int = 0
    remaining: int = 0


class OrgLimitsResult(sdl.Entity):
    limits: list[OrgLimit] = Field(default_factory=list)


class PublishPlatformEventParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    event_type: str = Field(..., description="API name of the Platform Event, e.g. 'Order_Placed__e'.")
    fields: dict = Field(default_factory=dict, description="Custom field API name -> value pairs for this event.")


class PlatformEventResult(sdl.Entity):
    event_type: str = ""
    success: bool = True
    replay_id: str = ""


# ─────────────────────────────────────────────────────────────────────────
# Tier 3 value-add: bulk connector-level operations + org health audit,
# same shape as MuleSoft/Automation Anywhere/UiPath/Blue Prism connectors.
# ─────────────────────────────────────────────────────────────────────────


class BulkRecordIdsParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")
    object_type: str = Field(..., description="sObject API name shared by every record id.")
    record_ids: list[str] = Field(..., description="Explicit record Ids; 1-200, never inferred.")


class BulkRecordResultItem(sdl.Entity):
    record_id: str = ""
    success: bool = True
    error: str = ""


class BulkRecordResult(sdl.Entity):
    results: list[BulkRecordResultItem] = Field(default_factory=list)


class AuditOrgParams(BaseModel):
    connection_id: str = Field("", description="Connection id; omitted uses the only/default connection.")


class OrgAuditRow(sdl.Entity):
    check: str = ""
    status: str = ""
    detail: str = ""


class OrgAuditReport(sdl.Entity):
    generated_at: str = ""
    rows: list[OrgAuditRow] = Field(default_factory=list)
