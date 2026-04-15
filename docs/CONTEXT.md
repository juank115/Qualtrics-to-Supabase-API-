# Contexto del Proyecto: Qualtrics → Supabase Pipeline

## Qué hace este proyecto
Pipeline 100% en la nube. Una Supabase Edge Function (`upload-survey`) descarga encuestas de Qualtrics, parsea los CSVs en memoria y hace upsert en PostgreSQL. pg_cron la dispara automáticamente cada 5 minutos. No requiere máquina local encendida ni script Python para operar.

```
pg_cron (*/5 * * * *)  →  Edge Function upload-survey  →  PostgreSQL
```

---

## Archivos del proyecto

| Archivo | Función |
|---|---|
| `sync_qualtrics_to_supabase.py` | Script de respaldo para ejecución manual |
| `recreate_tables_labels.sql` | SQL para recrear las 8 tablas con columnas descriptivas (`bigserial`) |
| `docs/ROADMAP_SUPABASE.md` | Guía completa de la integración |
| `docs/CLAUDE.md` | Referencia técnica rápida |
| `.env` | Variables locales para el script Python de respaldo |
| `requirements.txt` | Dependencias Python: `requests`, `python-dotenv`, `pandas` |

---

## Variables de entorno (`.env`) — solo para script Python de respaldo

```
QUALTRICS_API_TOKEN=...
QUALTRICS_DATA_CENTER=your_datacenter.iad1
SURVEYS_OUTPUT_PATH=./qualtrics_surveys
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
UPLOAD_SECRET_TOKEN=...
```

---

## Supabase

| Dato | Valor |
|---|---|
| Project URL | `https://your-project.supabase.co` |
| Edge Function | `upload-survey` — desplegada y activa |
| Método de inserción | Upsert via `supabase-js` con `onConflict: 'responseid'` |
| Automatización | pg_cron cada 5 minutos (`*/5 * * * *`) — job: `sync-qualtrics-5min` |

### Secrets configurados en la Edge Function
| Secret | Valor |
|---|---|
| `UPLOAD_SECRET_TOKEN` | Token de autenticación para callers |
| `SUPABASE_SERVICE_ROLE_KEY` | Acceso a PostgreSQL |
| `QUALTRICS_API_TOKEN` | Descarga de encuestas |
| `QUALTRICS_DATA_CENTER` | Your Qualtrics data center |

---

## Tablas en Supabase

Schema: `id bigserial primary key`, todas las demás columnas `text`.
Cada tabla tiene `UNIQUE constraint` en `responseid`.
Columnas con nombres descriptivos (labels de Qualtrics, snake_case, máx 60 chars).

| Tabla | Survey ID | Filas aprox. |
|---|---|---|
| `billing_caseagent_survey` | `<SURVEY_ID>` | ~681 |
| `care_caseagent_survey_gpst_zonar` | `<SURVEY_ID>` | ~4,202 |
| `csat_survey_legacy_gpst_customers` | `<SURVEY_ID>` | ~1,978 |
| `csat_survey_legacy_zonar_customers` | `<SURVEY_ID>` | ~610 |
| `legacy_gps_trackit_customer_upsell_opportunity_survey` | `<SURVEY_ID>` | ~322 |
| `legacy_zonar_customer_upsell_opportunity_survey` | `<SURVEY_ID>` | ~86 |
| `closed_loss_survey` | `<SURVEY_ID>` | ~13 |
| `closed_win_survey` | `<SURVEY_ID>` | ~21 |

---

## Decisiones técnicas

### Column labels desde row 1 de Qualtrics
El CSV de Qualtrics tiene 3 filas de header:
- Row 0: nombres cortos (`Q1`, `Q2`)
- Row 1: texto de la pregunta (`Did our team resolve your issue?`)
- Row 2: Import IDs (`{"ImportId": "..."}`)

El código usa row 1 cuando es descriptivo; si no, usa row 0. Resultado: columnas como `did_our_team_resolve_your_recent_issue` en lugar de `q1`.

### useLabels: true en export de Qualtrics
Los valores exportados son texto real ("Very Satisfied") en lugar de códigos numéricos (1, 2, 3).

### Edge Function autónoma
Contiene el pipeline completo: Qualtrics API → ZIP en memoria → CSV parseado → upsert. No depende del script Python.

### pg_cron en lugar de Deno.cron
`Deno.cron()` crashea en deploys desde el Dashboard. pg_cron se configura con SQL y no requiere cambios en el código de la función.

### Upsert en lugar de INSERT
Si `responseid` existe → actualiza; si es nuevo → inserta. Requiere `UNIQUE constraint` en `responseid`.

### Chunk size
200 filas por lote.

---

## Automatización activa (pg_cron)

```sql
-- Ver jobs activos
select * from cron.job;

-- Ver historial de ejecuciones
select * from cron.job_run_details order by start_time desc limit 10;

-- Eliminar
select cron.unschedule('sync-qualtrics-5min');
```

---

## Columnas calculadas (GENERATED ALWAYS AS STORED)

Se calculan automáticamente al insertar/actualizar. No requieren acción manual.

| Tabla | Columna | Tipo | Lógica |
|---|---|---|---|
| `care_caseagent_survey_gpst_zonar` | `calculated_score` | integer | Score por Q1 (LIKE '%Yes%') y Q3 (Positive/Neutral/Negative Experience) |
| `csat_survey_legacy_gpst_customers` | `nps_category` | text | Promoter (9-10) / Passive (7-8) / Detractor (0-6) |
| `csat_survey_legacy_gpst_customers` | `csat_satisfied` | integer | 1 si Satisfied/Very Satisfied, 0 otro caso |
| `csat_survey_legacy_zonar_customers` | `nps_category` | text | Misma lógica que gpst |
| `csat_survey_legacy_zonar_customers` | `csat_satisfied` | integer | Misma lógica (columna sin "the_") |

---

## Cron jobs de métricas (cada hora)

| Job | Tabla summary | Métricas |
|---|---|---|
| `update_csat_summary` | `csat_summary_csat_survey_legacy_gpst_customers` | csat_percentage, nps_score |
| `update_csat_summary_zonar` | `csat_summary_csat_survey_legacy_zonar_customers` | csat_percentage, nps_score |

```sql
SELECT * FROM csat_summary_csat_survey_legacy_gpst_customers;
SELECT * FROM csat_summary_csat_survey_legacy_zonar_customers;
```

---

## Nota sobre valores de Qualtrics (useLabels: true)

Los valores importados incluyen emojis de Qualtrics:
- Q1 care_caseagent: `👍/Yes`, `👎/No` — se usa `LIKE '%Yes%'`
- CSAT satisfaction: `😊\Satisfied`, `🤩\Very Satisfied`, etc. — se usa `ILIKE '%Satisfied%' AND NOT ILIKE '%Unsatisfied%'`
- NPS: valores numéricos 0-10

---

## Estado actual ✅ COMPLETADO

- [x] 8 tablas con columnas descriptivas (labels de row 1) y `bigserial primary key`
- [x] Edge Function `upload-survey` desplegada con `useLabels: true` y headers de row 1
- [x] Upsert con `onConflict: 'responseid'` funcionando
- [x] Secrets configurados: `UPLOAD_SECRET_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY`, `QUALTRICS_API_TOKEN`, `QUALTRICS_DATA_CENTER`
- [x] pg_cron activo — solo job `sync-qualtrics-5min` (jobs de prueba 30min y 1min eliminados)
- [x] Pipeline completo probado sin script Python
- [x] Script Python actualizado con `useLabels`, row 1 headers y 8 survey IDs
- [x] Columnas calculadas: calculated_score, nps_category, csat_satisfied
- [x] Cron jobs horarios: CSAT % y NPS para gpst y zonar customers

---

## Cómo ejecutar (manual, respaldo)

```bash
python sync_qualtrics_to_supabase.py
```

---

## Errores resueltos

| Error | Causa | Solución |
|---|---|---|
| `UnicodeEncodeError: '\u2192'` | Terminal Windows no soporta `→` | Reemplazado por `->` |
| `ValueError: Out of range float values (nan)` | NaN en columnas float | `df.astype(object).where(...)` |
| `500 duplicate key` | Edge Function usaba `insert` | Cambiado a `.upsert(..., { onConflict: 'responseid' })` |
| `401 No autorizado` | Token incorrecto en `.env` | Corregido `UPLOAD_SECRET_TOKEN` |
| `ReadTimeout 60s` | `Deno.cron()` crasheaba la función en deploy desde Dashboard | Eliminado `Deno.cron`, reemplazado por pg_cron |
| `schema "cron" does not exist` | Extensión pg_cron no activada | Activada en Dashboard → Database → Extensions |
| `schema "net" does not exist` | Extensión pg_net no activada | Activada en Dashboard → Database → Extensions |
| `42601 syntax error at bigint` | `generated always as identity` no compatible con Dashboard | Cambiado a `bigserial primary key` |
| `q1` mostraba número en lugar de texto | `useLabels` no activado | Agregado `useLabels: true` al export payload |
| `Could not find column 'how_was_your_experience...'` | `sanitize_name` no truncaba a 60 chars | Agregado `[:max_len]` con `max_len=60` en Python y Edge Function |
