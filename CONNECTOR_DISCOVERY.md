# Salesforce — Connector Discovery

**Дата discovery:** 2026-08-20
**Статус:** Ярусы 1-3 пройдены. §6 (решение по объёму) — **исключение Шага 5 применено**: Влад заявил «максимальный функционал, полный максимум» первым же сообщением по этому коннектору (задача #2194) — переспрашивать форму релиза не требуется, зафиксировано ниже.

---

## 1. Целевой сервис и источники

Salesforce — доминирующий CRM/enterprise-платформа (Lead/Contact/Account/Opportunity/Case + кастомные объекты). В отличие от Notion/Trello/Asana, у Salesforce **нет единого API** — портфель из 9 разных API-поверхностей под разные сценарии.

Источники (прочитаны/перепроверены 2026-08-20, плюс собственный research от 2026-08-03 — `Docs/session-notes/salesforce-api-imperal-connector-research.md`):
- `developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/` — REST API Developer Guide (sforce_rest_api, resources_list, dome_versions, dome_sobject_describe, using_resources_working_with_records)
- `developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/openapi_beta.htm` — OpenAPI 3.0 для sObjects REST API (статус: **beta**, генерируется по org, не статичен — не используем как единственный источник)
- `help.salesforce.com` — OAuth 2.0 Client Credentials Flow for Server-to-Server Integration (xcloud.remoteaccess_oauth_client_credentials_flow), Configure Client Credential Flow Policies
- `developer.salesforce.com/blogs/2023/03/using-the-client-credentials-flow-for-easier-api-authentication`
- `softwareinsights.dev` — подтверждение: Username-Password OAuth Flow **retired в Winter '27** — не проектировать на нём ничего
- Текущая версия REST API на дату discovery: **Summer '26 (v67.0)**, Winter '27 (v68.0) в preview

### Модель данных
Всё — **sObjects** (аналог таблиц): стандартные (`Account`, `Contact`, `Lead`, `Opportunity`, `Case`, `Task`, `Event`, `Campaign`, `User`, `Report`, `Dashboard`…) + кастомные объекты клиента (суффикс `__c`). Запросы — через **SOQL** (`SELECT Id, Name FROM Account WHERE ...`), полнотекстовый поиск — через **SOSL**.

### Авторизация — решённый вопрос архитектуры
Salesforce **не входит** в список встроенных `ext.oauth` провайдеров платформы (`google`/`microsoft`/`yahoo` — `ctx.oauth_authorize_url` иначе кидает ValueError). Варианты:
- ❌ Web Server Flow (redirect-based) — потребовал бы hand-rolled `@ext.webhook("/callback")`, лишняя сложность.
- ❌ Username-Password Flow (`grant_type=password`) — **официально уходит в retirement в Winter '27**, проектировать на нём нельзя.
- ✅ **OAuth 2.0 Client Credentials Flow** (Salesforce, начиная с 2023, `help.salesforce.com/.../remoteaccess_oauth_client_credentials_flow`) — server-to-server, БЕЗ редиректа пользователя. Connected App настраивается с политикой "Run As" (выделенный integration user), клиент шлёт `client_id`+`client_secret`+`grant_type=client_credentials` на `https://<my-domain>.my.salesforce.com/services/oauth2/token` и получает `access_token` + **`instance_url`** (URL конкретной org клиента — специфика Salesforce, не фиксированный домен как у Google).

**Итоговая модель, идентичная уже проверенному паттерну MuleSoft/Power Automate/n8n/Make (BYOK):** пользователь создаёт свой Connected App в СВОЕЙ Salesforce org (Setup → App Manager → New Connected App, включает OAuth, включает Client Credentials Flow, назначает Run As User), вставляет в коннектор `client_id`, `client_secret` и свой **My Domain URL** (например `mycompany.my.salesforce.com`). Коннектор сам обменивает это на access_token+instance_url, кэширует и обновляет по истечении (access_token живёт ограниченное время, no refresh_token в этом flow — токен просто перезапрашивается по тем же client credentials). Один секрет — JSON-массив нескольких org на пользователя (тот же паттерн, что у MuleSoft/Slack/Power Automate).

### Лимиты
Daily per-org API call limit зависит от edition (Developer ~15k/день, Enterprise ~1млн+, Unlimited ×10). Превышение → `REQUEST_LIMIT_EXCEEDED`. Bulk API 2.0 существует отдельно именно для избежания этого лимита на больших объёмах (>2000 записей рекомендуется).

---

## 2. Карта возможностей (направление на каждую)

| Домен API | Возможность | Ingress/Egress/Both | Комментарий |
|---|---|---|---|
| **REST — sObjects CRUD** | Create/Read/Update/Delete/Upsert (по внешнему id) любого sObject, стандартного или кастомного | Both | Ядро коннектора |
| **REST — sObject Describe** | Метаданные объекта: поля, типы, picklist-значения, обязательность | Ingress | Нужно для generic-объектной модели (кастомные объекты клиента) |
| **REST — Query (SOQL)** | Произвольный SELECT-запрос по данным org | Ingress | Ключевая функция — гибче, чем набор жёстких list_* |
| **REST — Search (SOSL)** | Полнотекстовый поиск по нескольким объектам сразу | Ingress | |
| **REST — Composite/Batch** | Несколько разных операций в ОДНОМ HTTP-запросе (до 25 sub-запросов в /composite, до 2000 в /composite/batch) | Both | Родная batch-возможность Salesforce — не наша обёртка |
| **Bulk API 2.0** | Массовые insert/update/upsert/delete/query (>10k записей, до 150млн/job), CSV/JSON, асинхронно | Both | Для тяжёлых операций (импорт лидов, массовое обновление) |
| **Connect REST API — Chatter** | Feed posts, comments, likes, files на записях | Both | Социальный слой поверх CRM-записей |
| **Connect REST API — Files (ContentVersion)** | Upload/download/attach файлов к записям | Both | |
| **Reports & Dashboards API** | Список/чтение отчётов и дашбордов, запуск отчёта с фильтрами | Ingress | Готовая аналитика Salesforce, не своя |
| **Approval Process API** | Submit for approval / approve / reject запись | Both | Процессный слой (например, скидка требует утверждения) |
| **Platform Events (publish)** | Публикация кастомного события в шину Salesforce (без подписки — это уже Streaming/Pub-Sub) | Egress | Публикация проста через REST; подписка требует gRPC/CometD — сложнее |
| **Streaming/Pub-Sub API (subscribe)** | Real-time подписка на изменения записей / custom events | Ingress | gRPC+Protobuf — не совместимо с обычным REST-хендлером синхронного тула; **not applicable** для этого захода |
| **Email — SingleEmailMessage** | Отправка email через Salesforce (с шаблоном или без) от имени интеграции | Egress | |
| **Metadata API (deploy)** | Деплой пакетов метаданных между org (sandbox→prod) | Egress | XML/SOAP, deploy-пакеты — не про работу с данными; **not applicable** |
| **Tooling API** | Разработка (Apex classes, debug logs) | Both | Dev-инструмент, не бизнес-функция CRM; **not applicable** |
| **SOAP API** | Legacy строго типизированный клиент | Both | REST полностью покрывает то же; **not applicable** (дублирует REST) |
| **Admin — Users** | List/read/deactivate пользователей org | Both | Полезно для аудита доступа |
| **Admin — Permission Sets/Profiles** | Список ролей/прав | Ingress | Read-only справочно |
| **Duplicate/Territory Management** | Управление правилами дублей и территорий | Both | Узкоспециализированная enterprise-фича; **not applicable** для этого захода |
| **Knowledge Articles** | CRUD статей базы знаний (Salesforce Knowledge) | Both | Требует отдельной лицензии Knowledge — не у всех org есть; **deferred** |

---

## 3. Ярус 1 — Ключевые функции (P0-кандидаты)

Минимальный набор, без которого коннектор не решает основную боль (просмотр/управление CRM-данными из Imperal):
1. `connect_salesforce` — Client Credentials Flow (client_id + client_secret + My Domain URL), проверка живым запросом
2. `list_accounts`, `get_account`, `create_account`, `update_account`
3. `list_contacts`, `get_contact`, `create_contact`, `update_contact`
4. `list_leads`, `get_lead`, `create_lead`, `update_lead`, `convert_lead`
5. `list_opportunities`, `get_opportunity`, `create_opportunity`, `update_opportunity`
6. `list_cases`, `get_case`, `create_case`, `update_case`
7. `run_soql_query` — универсальный доступ к любым данным org
8. `describe_object` — метаданные объекта (в т.ч. кастомного)

## 4. Ярус 2 — Полное покрытие

| Возможность | Статус | Причина/триггер |
|---|---|---|
| Tasks/Events CRUD (активности) | included | Естественное продолжение CRM-модели (follow-up задачи на Lead/Opportunity) |
| Campaigns CRUD + CampaignMember | included | Маркетинговый слой, часто нужен вместе с Lead |
| SOSL полнотекстовый поиск (`search_records`) | included | Дешёвая добавка поверх уже реализованного REST-клиента |
| Composite/Batch запрос (родной Salesforce batch) | included | Уже есть в REST API, не наша обёртка — просто expose |
| Bulk API 2.0 — bulk create/update/upsert/delete по CSV/JSON | included | Явно требуется для "максимума"; тяжёлые операции |
| Chatter — post feed item, list feed, comment | included | |
| Files — upload/attach/download ContentVersion к записи | included | |
| Reports & Dashboards — list/get/run с фильтрами | included | |
| Approval Process — submit/approve/reject | included | |
| Email — send SingleEmailMessage | included | |
| Users/Profiles/Permission Sets — read-only list | included | Аудит доступа, admin-обзор |
| Platform Events — publish custom event | included | Публикация через обычный REST POST, без подписки |
| Platform Events / Streaming — subscribe (real-time) | not applicable | Требует gRPC/CometD-подписки, несовместимо с request/response tool-моделью коннектора в этом заходе |
| Metadata API (deploy sandbox→prod) | not applicable | XML/SOAP deploy-пакеты — вне модели "данные CRM", ближе к DevOps-инструменту (как Design Center у MuleSoft) |
| Tooling API (Apex classes/debug logs) | not applicable | Инструмент разработчика Salesforce, не бизнес-функция CRM |
| SOAP API | not applicable | Полностью дублируется REST API |
| Territory/Duplicate Management | deferred | Узкая enterprise-настройка; добавить по явному запросу |
| Knowledge Articles CRUD | deferred | Требует отдельной лицензии Salesforce Knowledge, не у всех org включена; добавить по явному запросу |

## 5. Ярус 3 — Функции на нашей стороне (Imperal-side value-add)

1. **`bulk_update_records`** — универсальная bulk-обёртка над Composite/Bulk API для ЛЮБОГО sObject (не только стандартных) по explicit id-списку, с preview/apply-паттерном для деструктивных операций (тот же принцип, что WordPress Hub `preview_bulk_*`/`apply_bulk_*`) — у самого Salesforce Bulk API нет встроенного dry-run.
2. **`audit_org_health`** — агрегированный отчёт по org: сколько записей каждого ключевого объекта, сколько Lead без owner, сколько просроченных Opportunity (Close Date в прошлом, но не Closed), сколько открытых Case без активности N дней — аналог `audit_cloudhub_environment`/`audit_folder` у RPA-коннекторов, которого нет как готового отчёта в самом Salesforce.
3. **`pipeline_snapshot`** — свод по воронке продаж: сумма Opportunity по стадиям (Stage), weighted pipeline (Amount × Probability), сравнение с прошлым периодом — Salesforce отдаёт сырые записи, но не готовый управленческий срез без настройки отдельного Report.
4. **`convert_lead_with_followup`** — конвертация Lead в Account/Contact/Opportunity одним вызовом с опциональным немедленным созданием Task-напоминания — нативный `convert_lead` Salesforce не создаёт follow-up сам.
5. **`find_stale_opportunities`** — Opportunity без активности (LastActivityDate) дольше N дней, отсортированные по Amount — типовой sales-ops запрос, которого нет как единого API-эндпоинта.

---

## 6. Решение по объёму этого захода

**Выбрано: Ярус 1 + Ярус 2 + Ярус 3 (полное покрытие плюс value-add) — «максимум».**

Подтверждено Владом первым же сообщением по этому коннектору: *«приступай к разработке приложения Salesforce. максимальный функционал, полный максимум»* (2026-08-20, до создания задачи #2194) — исключение Шага 5 `CONNECTOR_DISCOVERY_STANDARD.md` применено, отдельный вопрос о форме релиза не задавался.
