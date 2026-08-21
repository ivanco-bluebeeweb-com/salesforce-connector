# Salesforce Connector — Preparation

**Статус:** Фаза 1 (Discovery + архитектурные решения) завершена. Влад
подтвердил объём релиза 2026-08-20 — «максимальный функционал, полный
максимум» (Ярус 1+2+3), первым же сообщением по этому коннектору, до
создания задачи. Исключение Шага 5 `CONNECTOR_DISCOVERY_STANDARD.md`
применено — отдельный вопрос об объёме не задавался.

**Владелец продукта:** vlad@bluebeeweb.com
**Дата подготовки:** 2026-08-20, v0.1
**Vikunja task:** #2194 (BBW Imperal Apps), [App Development].

**Почему сейчас:** Salesforce — крупнейшая CRM-платформа в мире (~20%
оценочной доли рынка, №1 в сегменте). В портфеле Imperal сейчас нет ни
одного CRM-коннектора (проверено `search_marketplace` 2026-08-20 — 34
приложения, ни одного CRM). Открывает совершенно новый класс данных для
маркетплейса — sales/CRM (Lead/Contact/Account/Opportunity/Case), в
отличие от уже собранной серии iPaaS-коннекторов (MuleSoft/Workato/n8n/
Make/Power Automate/Zapier), которые оркестрируют workflow, а не
управляют бизнес-записями.

---

## 1. Паспорт приложения

**Название в Marketplace (display_name): «Salesforce»**. Внутренний
app_id/папка: `salesforce-connector`.

**Salesforce Connector** — коннектор к Salesforce CRM через REST API
(sObjects CRUD, SOQL/SOSL), Bulk API 2.0 (массовые операции), Connect
REST API (Chatter, Files), Reports & Dashboards API, Approval Process
API. BYOK: пользователь подключает свою собственную Salesforce
организацию через собственный Connected App (OAuth 2.0 Client
Credentials Flow). Imperal ничего не хостит и не проксирует помимо
самого запроса.

---

## 2. Ключевые факты о Salesforce API (см. `CONNECTOR_DISCOVERY.md`)

### 2.1 Нет единого API — выбраны REST + Bulk 2.0 + Connect как основные домены

У Salesforce 9 разных API-поверхностей (REST, SOAP, Bulk 2.0, Tooling,
Metadata, Streaming, Pub/Sub, Connect REST, Apex REST). Discovery выделил
как релевантные:

- **REST API** (`/services/data/v{version}/`) — ВЫБРАН как основной.
  sObjects CRUD (create/read/update/delete/upsert), SOQL query, SOSL
  search, sObject Describe, Composite/Batch.
- **Bulk API 2.0** — массовые операции (>10k записей, до 150млн/job),
  для тяжёлых импортов/обновлений.
- **Connect REST API** — Chatter (feed/comments), Files (ContentVersion).
- **Reports & Dashboards API** — чтение готовой аналитики org.
- **Approval Process API** — submit/approve/reject бизнес-процессов.
- Вне охвата (`not applicable`, см. Discovery §4): Streaming/Pub-Sub
  (gRPC/CometD, несовместимо с request/response tool-моделью), Metadata
  API (deploy sandbox→prod, DevOps-инструмент), Tooling API (Apex
  разработка), SOAP API (полностью дублируется REST).

### 2.2 sObject — реальная модель данных

Подтверждено `developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/`:

- Endpoint base: `https://<instance>.my.salesforce.com/services/data/v67.0/`
- Ключевые операции: `GET /sobjects/{Object}/{id}` (get), `POST
  /sobjects/{Object}` (create), `PATCH /sobjects/{Object}/{id}` (update),
  `DELETE /sobjects/{Object}/{id}` (delete), `PATCH
  /sobjects/{Object}/{extIdField}/{extId}` (upsert по внешнему id),
  `GET /sobjects/{Object}/describe` (метаданные полей).
- Запросы: `GET /query/?q={SOQL}` и `GET /search/?q={SOSL}`.
- `Id` — 18-символьный уникальный идентификатор записи (case-insensitive
  версия 15-символьного, используем 18-символьную везде).
- Кастомные объекты — суффикс `__c` (объект и поля), стандартные объекты
  без суффикса (`Account`, `Contact`, `Lead`, `Opportunity`, `Case`,
  `Task`, `Event`, `Campaign`, `CampaignMember`, `User`).
- Composite: `POST /composite` (до 25 sub-запросов, с зависимостями через
  `@{refId.field}`), `POST /composite/batch` (до 25 независимых,
  Bulk-подобный `/composite/sobjects` до 200 записей за раз).

### 2.3 Auth — OAuth 2.0 Client Credentials Flow, без курицы-и-яйца

Salesforce поддерживает Client Credentials Flow с 2023
(`help.salesforce.com/.../remoteaccess_oauth_client_credentials_flow`) —
server-to-server, без редиректа. Connected App создаётся немедленно в
Setup → App Manager любым админом org, никакого внешнего ревью Salesforce
не требуется (как MuleSoft, в отличие от Zapier). Токен: `POST
https://<my-domain>.my.salesforce.com/services/oauth2/token` с
`grant_type=client_credentials`, `client_id`, `client_secret`. Ответ
содержит `access_token` + **`instance_url`** (URL конкретной org — не
фиксированный домен, специфика Salesforce в отличие от Google/Microsoft).
Нет refresh_token в этом flow — токен просто перезапрашивается по тем же
credentials при истечении/401.

**Официально подтверждено retirement Username-Password Flow в Winter
'27** — не используется нигде в этом коннекторе.

Требуемые поля от пользователя:
1. `client_id` — Connected App Consumer Key
2. `client_secret` — Connected App Consumer Secret
3. `my_domain_url` — My Domain хоста org (например `mycompany.my.salesforce.com`)
4. `label` (опционально) — поддержка нескольких org на одного пользователя
   (JSON-массив в одном секрете, тот же паттерн, что у MuleSoft/Power
   Automate/Slack — `ctx.secrets` не имеет примитива "один секрет на id")

### 2.4 Bulk API 2.0 — второй домен (Ярус 2)

`POST /services/data/v67.0/jobs/ingest` (создать job) → `PUT
.../jobs/ingest/{jobId}/batches` (залить CSV) → `PATCH
.../jobs/ingest/{jobId}` (`state: UploadComplete`) → опрос статуса `GET
.../jobs/ingest/{jobId}`. Асинхронный процесс — коннектор оборачивает это
в create+poll паттерн, аналогичный CSV-импорту WordPress Hub
(`preview_csv_catalog_import`/`apply_csv_catalog_import`).

---

## 3. Решённые архитектурные вопросы

| # | Вопрос | Решение | Обоснование |
|---|---|---|---|
| 1 | BYOK или центральный брокер? | **BYOK**, как MuleSoft/Make.com/n8n/Power Automate | Пользователь управляет своей Salesforce-организацией; Imperal не хостит и не проксирует CRM-данные клиента. |
| 2 | Какой домен API основной? | **REST API** (sObjects CRUD/SOQL/SOSL) как ядро, **Bulk API 2.0** для тяжёлых операций, **Connect REST** (Chatter/Files), **Reports & Dashboards**, **Approval Process** как вторичные | Полное покрытие бизнес-функций CRM без DevOps/инфраструктурных API. |
| 3 | Auth механизм? | **Connected App, OAuth 2.0 Client Credentials Flow** | Официально поддерживается с 2023, server-to-server без редиректа, создаётся немедленно без внешнего ревью. Username-Password Flow исключён (retirement Winter '27). |
| 4 | Сколько секретов? | **Три + label**: `client_id`, `client_secret`, `my_domain_url` | Все обязательны для аутентификации и адресации конкретной org. |
| 5 | Объём релиза? | **«Максимум» = Ярус 1+2+3** | Решение Влада 2026-08-20, заявлено первым сообщением, исключение Шага 5 применено. |
| 6 | Streaming/Pub-Sub (real-time подписки)? | **Вне охвата P0**, `not applicable` в CONNECTOR_DISCOVERY.md | gRPC+Protobuf/CometD несовместимы с синхронной request/response tool-моделью коннектора. |
| 7 | Metadata API (deploy sandbox→prod), Tooling API, SOAP API? | **Вне охвата**, `not applicable` | DevOps/разработческий инструментарий, не бизнес-функции CRM; REST полностью дублирует SOAP. |
| 8 | Territory/Duplicate Management, Knowledge Articles? | **Deferred** | Узкая enterprise-настройка / отдельная лицензия Salesforce Knowledge — не у всех org есть; добавить по явному запросу. |

---

## 4. Функциональный охват («максимум» = Ярус 1+2+3)

### Ярус 1 (P0 — ключевые функции)
- `connect_salesforce` (client_id, client_secret, my_domain_url, label) — проверка живым запросом + сохранение через `ctx.secrets`
- `disconnect_salesforce`, `list_connections`
- `list_accounts` / `get_account` / `create_account` / `update_account` / `delete_account`
- `list_contacts` / `get_contact` / `create_contact` / `update_contact` / `delete_contact`
- `list_leads` / `get_lead` / `create_lead` / `update_lead` / `delete_lead` / `convert_lead`
- `list_opportunities` / `get_opportunity` / `create_opportunity` / `update_opportunity` / `delete_opportunity`
- `list_cases` / `get_case` / `create_case` / `update_case` / `delete_case`
- `run_soql_query` — универсальный доступ к любым данным org
- `describe_object` — метаданные объекта (в т.ч. кастомного `__c`)

### Ярус 2 (полное покрытие)
- `list_tasks` / `create_task` / `update_task` / `complete_task`
- `list_events` / `create_event` / `update_event`
- `list_campaigns` / `create_campaign` / `add_campaign_member`
- `search_records` (SOSL, полнотекстовый поиск по нескольким объектам)
- `run_composite_request` (родной Salesforce batch, до 25 sub-запросов)
- `bulk_create_records` / `bulk_update_records` / `bulk_upsert_records` / `bulk_delete_records` (Bulk API 2.0, create+poll)
- `get_bulk_job_status`
- `post_chatter_feed_item` / `list_chatter_feed` / `comment_on_feed_item`
- `upload_file_to_record` / `list_record_files`
- `list_reports` / `run_report`
- `list_dashboards` / `get_dashboard`
- `submit_for_approval` / `process_approval_request`
- `send_email` (SingleEmailMessage)
- `list_users` / `get_user` / `deactivate_user`
- `list_permission_sets` / `list_profiles`
- `publish_platform_event`

### Ярус 3 (наш value-add)
- `bulk_update_records_by_filter` — deferred внутри bulk_update_records (см. ниже) — на самом деле реализуем как `preview_bulk_field_update` / `apply_bulk_field_update` (preview/apply над explicit id-списком, dry-run которого нет в самом Bulk API)
- `audit_org_health` — агрегированный отчёт: записи по ключевым объектам, Lead без owner, просроченные Opportunity, застрявшие Case
- `pipeline_snapshot` — свод по воронке продаж (сумма по Stage, weighted pipeline)
- `convert_lead_with_followup` — конвертация Lead + немедленный follow-up Task одним вызовом
- `find_stale_opportunities` — Opportunity без активности дольше N дней

---

## 5. Открытые вопросы для Влада

Нет открытых вопросов — объём релиза подтверждён 2026-08-20 («максимум»).

---

## 6. Журнал проверки дублей

`search_marketplace` по «Salesforce»/«CRM» — дублей не найдено в
существующем портфеле Imperal на момент 2026-08-20 (34 приложения, ни
одного CRM-коннектора).
