# Roadmap: Integración de Qualtrics con Supabase

Pipeline 100% en la nube. pg_cron dispara la Edge Function `upload-survey` cada 5 minutos. La función descarga de Qualtrics con labels, parsea en memoria y hace upsert en PostgreSQL.

## Arquitectura Final

```
pg_cron (*/5 * * * *)
  → POST /functions/v1/upload-survey  {"trigger_full_sync": true}
  → Qualtrics API (useLabels: true)  →  ZIP en memoria  →  CSV parseado  →  upsert PostgreSQL

[Respaldo manual]
  python sync_qualtrics_to_supabase.py
  → POST /functions/v1/upload-survey  {"table_name": ..., "records": [...]}
  → upsert PostgreSQL
```

---

## Fase 1: Configuración de Supabase ✅

### 1.1 Proyecto
- **Project URL:** `https://your-project.supabase.co`
- En **Settings → API**: Project URL y Service Role Key.

### 1.2 Variables locales (`.env`)
```
QUALTRICS_API_TOKEN=...
QUALTRICS_DATA_CENTER=your_datacenter.iad1
SURVEYS_OUTPUT_PATH=./qualtrics_surveys
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
UPLOAD_SECRET_TOKEN=...
```

### 1.3 Secrets en la Edge Function
Configurados en **Dashboard → Edge Functions → upload-survey → Secrets**:
```
UPLOAD_SECRET_TOKEN=...
SUPABASE_SERVICE_ROLE_KEY=...
QUALTRICS_API_TOKEN=...
QUALTRICS_DATA_CENTER=your_datacenter.iad1
```

---

## Fase 2: Tablas en la Base de Datos ✅

### 2.1 Recrear tablas con columnas descriptivas
Ejecutar `recreate_tables_labels.sql` en **Database → SQL Editor → New query**.

El SQL incluye `DROP TABLE IF EXISTS` + `CREATE TABLE` + `UNIQUE constraint` para las 8 tablas.
Columnas usan labels descriptivos del texto de las preguntas (row 1 del CSV de Qualtrics).

### 2.2 Schema
- `id bigserial primary key`
- Todas las demás columnas: `text`
- `UNIQUE constraint` en `responseid` en cada tabla

### 2.3 Tablas
| Tabla | Survey ID |
|---|---|
| `billing_caseagent_survey` | `<SURVEY_ID>` |
| `care_caseagent_survey_gpst_zonar` | `<SURVEY_ID>` |
| `csat_survey_legacy_gpst_customers` | `<SURVEY_ID>` |
| `csat_survey_legacy_zonar_customers` | `<SURVEY_ID>` |
| `legacy_gps_trackit_customer_upsell_opportunity_survey` | `<SURVEY_ID>` |
| `legacy_zonar_customer_upsell_opportunity_survey` | `<SURVEY_ID>` |
| `closed_loss_survey` | `<SURVEY_ID>` |
| `closed_win_survey` | `<SURVEY_ID>` |

---

## Fase 3: Script Python (respaldo manual) ✅

Ya no es necesario para la automatización. Útil para forzar un sync manual.

Cambios aplicados:
- `useLabels: true` en el payload del export
- Row 1 del CSV como headers de columna
- `sanitize_name` trunca a 60 chars (`[:60]`) para coincidir con columnas de la tabla
- 8 survey IDs

```bash
python sync_qualtrics_to_supabase.py
```

Dependencias: `pip install pandas requests python-dotenv`

---

## Fase 4: Edge Function `upload-survey` ✅

Desplegada en **Dashboard → Edge Functions → upload-survey**.

### Comportamiento del export
- `useLabels: true` → valores como "Very Satisfied" en lugar de "1"
- Row 1 → column headers descriptivos ("did_our_team_resolve_your_recent_issue")
- Row 0 → fallback para columnas de metadata (responseid, startdate, etc.)

### Modos de operación

**Modo A — Sync completo (pg_cron)**
```json
{ "trigger_full_sync": true }
```

**Modo B — Upload por tabla (Python)**
```json
{ "table_name": "nombre_tabla", "records": [...] }
```

### Headers requeridos
```
Authorization: Bearer <UPLOAD_SECRET_TOKEN>
Content-Type: application/json
```

### Tecnologías
- `@supabase/supabase-js` — upsert a PostgreSQL
- `fflate` — descompresión del ZIP en memoria
- `deno std csv` — parseo de CSV en memoria

---

## Fase 5: Automatización con pg_cron ✅

### Extensiones requeridas (activar una sola vez)
**Dashboard → Database → Extensions** → activar `pg_cron` y `pg_net`.

### Cron job activo
```sql
-- Job actual (cada 5 minutos)
select cron.schedule(
  'sync-qualtrics-5min',
  '*/5 * * * *',
  $$
  select net.http_post(
    url     := 'https://your-project.supabase.co/functions/v1/upload-survey',
    headers := '{"Authorization": "Bearer <YOUR_UPLOAD_SECRET_TOKEN>", "Content-Type": "application/json"}'::jsonb,
    body    := '{"trigger_full_sync": true}'::jsonb
  );
  $$
);
```

### Gestión del cron
```sql
-- Ver jobs activos
select * from cron.job;

-- Ver historial de ejecuciones
select * from cron.job_run_details order by start_time desc limit 10;

-- Eliminar
select cron.unschedule('sync-qualtrics-5min');
```

---

## Resumen de Seguridad

| Práctica | Implementación |
|---|---|
| Credenciales fuera del código | `.env` local + Supabase Secrets |
| Credenciales DB nunca en Python | Python solo conoce `UPLOAD_SECRET_TOKEN` |
| Upsert idempotente | Ejecutar N veces produce el mismo resultado |
| Sin exposición frontend | Todo corre en Supabase internamente |
| Reintentos en Python | 3 intentos, 3s entre cada uno |

---

## Fase 6: Columnas Calculadas y Métricas ✅

### 6.1 Columnas generadas (GENERATED ALWAYS AS STORED)

Se calculan automáticamente al insertar/actualizar filas. No requieren acción manual.

**`care_caseagent_survey_gpst_zonar`**
- `calculated_score` (integer) — Score basado en Q1 (resolución: `LIKE '%Yes%'`) y Q3 (experiencia: Positive/Neutral/Negative Experience)
- SQL: `add_score_column.sql`

**`csat_survey_legacy_gpst_customers`**
- `nps_category` (text) — Promoter (9-10) / Passive (7-8) / Detractor (0-6)
- `csat_satisfied` (integer) — 1 si Satisfied/Very Satisfied, 0 en otro caso

**`csat_survey_legacy_zonar_customers`**
- `nps_category` (text) — Misma lógica que gpst
- `csat_satisfied` (integer) — Misma lógica. Columna fuente: `how_would_you_rate_your_satisfaction_with_zonar` (sin "the_")

### 6.2 Cron jobs de métricas (cada hora)

| Job | Tabla summary | Métricas |
|---|---|---|
| `update_csat_summary` | `csat_summary_csat_survey_legacy_gpst_customers` | CSAT: 71.4%, NPS: 22.4 |
| `update_csat_summary_zonar` | `csat_summary_csat_survey_legacy_zonar_customers` | CSAT: 79.4%, NPS: 29.0 |

```sql
-- Consultar métricas
SELECT * FROM csat_summary_csat_survey_legacy_gpst_customers;
SELECT * FROM csat_summary_csat_survey_legacy_zonar_customers;
```

### 6.3 Nota sobre valores de Qualtrics (useLabels: true)

Los valores importados incluyen emojis de Qualtrics:
- Q1 care_caseagent: `👍/Yes`, `👎/No` — se usa `LIKE '%Yes%'` para matchear
- CSAT satisfaction: `😊\Satisfied`, `🤩\Very Satisfied`, etc. — se usa `ILIKE '%Satisfied%' AND NOT ILIKE '%Unsatisfied%'`
- NPS: valores numéricos 0-10 (sin emojis)

---

## Estado Final ✅ COMPLETADO

| Componente | Estado |
|---|---|
| 8 tablas con columnas descriptivas y `bigserial` | ✅ |
| Edge Function con `useLabels: true` y headers de row 1 | ✅ |
| Upsert con `onConflict: 'responseid'` | ✅ |
| Secrets de Qualtrics y Supabase en Edge Function | ✅ |
| Pipeline completo probado (8 surveys, ~7,913 filas) | ✅ |
| pg_cron activo cada 5 minutos (`sync-qualtrics-5min`) | ✅ |
| Script Python actualizado como respaldo | ✅ |
| Columnas calculadas: calculated_score, nps_category, csat_satisfied | ✅ |
| Cron jobs de métricas CSAT % y NPS (cada hora) | ✅ |
