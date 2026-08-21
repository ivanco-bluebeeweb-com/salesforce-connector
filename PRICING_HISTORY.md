# Salesforce Connector — история цен

## 2026-08-21 — первичный прайсинг (per_action, revenue_split_dev=95)

Применено через `developer.update_pricing` (pricing_config как настоящий
вложенный JSON, `revenue_split_dev=95` явным параметром вызова — partner
tier, тот же, что был установлен при `developer.create_app`), затем
`developer.deploy_app` для синхронизации зеркала в БД платформы. Порядок
соблюдён строго по канону `PRICING_POLICY.md`: код готов → пост-аудит
чистый (SDK validate: 0 errors/0 warnings) → deploy_app → update_pricing
→ deploy_app (повторно).

Шкала — фиксированная {0, 8, 16, 20, 40, 60} токенов, без исключений.
38/38 функций манифеста покрыты ровно одной ценой (проверено
программно: `manifest tools == priced tools`, ноль пропусков, ноль
лишних, ноль недопустимых значений).

Обоснование по категориям:

- **0 (бесплатно)** — `connect_salesforce`, `disconnect_salesforce`,
  `list_connections`: настройка/удаление доступа и чтение уже
  сохранённого локально списка подключений — брать деньги нечестно, это
  не запрос к внешнему API за данными.

- **8 (простое чтение)** — `get_record`, `describe_object`,
  `list_objects`, `run_soql`, `continue_soql`, `run_sosl`, `get_bulk_job`,
  `list_bulk_jobs`, `get_bulk_job_results`, `list_chatter_feed`,
  `list_record_files`, `list_reports`, `run_report`, `list_dashboards`,
  `get_dashboard`, `list_approval_work_items`, `get_org_limits`: каждое —
  одиночный read-запрос к Salesforce REST/Bulk/Connect/Reports API, без
  агрегации по многим объектам.

- **16 (стандартный одиночный write)** — `create_record`,
  `update_record`, `upsert_record`, `delete_record`, `post_chatter_feed`,
  `comment_on_feed`, `upload_file`, `submit_for_approval`,
  `process_approval`, `send_email`, `abort_bulk_job`: создание/изменение/
  удаление ОДНОЙ сущности за вызов.

- **20 (существенная одиночная операция)** — `run_composite` (до 25
  sub-запросов в одном атомарном HTTP-вызове — тяжелее обычного write),
  `create_bulk_job` (запускает реальную фоновую job-обработку в проде
  пользователя прямо сейчас), `convert_lead` (многошаговый процесс
  Salesforce: Account+Contact+Opportunity за одну операцию),
  `publish_platform_event` (немедленно триггерит подписчиков события в
  реальном времени в чужом орге).

- **40 (тяжёлая диагностика/агрегирующий отчёт)** — `audit_org`:
  комбинирует org limits + подсчёты записей по нескольким объектам в
  одном вызове — полный health-снепшот, а не одно чтение.

- **60 (bulk/batch)** — `bulk_update_records`, `bulk_delete_records`:
  та же write-операция, повторённая до 200 раз за один вызов.

Сверка с деплоем: `developer.deploy_app` после прайсинга по-прежнему
показывает `validation: 20/21` — но это НЕ связано с прайсингом и НЕ
блокер: тот же самый `deploy_app`, вызванный на уже опубликованном
эталонном MuleSoft Connector (полностью прайсованном, живом
приложении), тоже возвращает `20/21`. Значит 20/21 — нормальное,
ожидаемое состояние платформенной проверки деплоя для этого класса
приложений, а не дефект Salesforce Connector.
