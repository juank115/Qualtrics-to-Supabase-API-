# Qualtrics → Supabase Pipeline

## Project Overview
Fully automated pipeline running 100% in the cloud. A Supabase Edge Function (`upload-survey`) downloads survey data from Qualtrics, processes it, and upserts it to PostgreSQL every 5 minutes via pg_cron. No local machine or Python script required for normal operation.

## Architecture

```
pg_cron (*/5 * * * *)
  → POST /functions/v1/upload-survey  {"trigger_full_sync": true}
  → Edge Function: downloads from Qualtrics, parses CSV with labels, upserts to PostgreSQL
```

```
[Manual backup]  python sync_qualtrics_to_supabase.py
  → POST /functions/v1/upload-survey  {"table_name": ..., "records": [...]}
  → Edge Function: upserts received records to PostgreSQL
```

## Edge Function: `upload-survey` ✅

**URL:** `https://your-project.supabase.co/functions/v1/upload-survey`

### Mode A — Full sync (used by pg_cron)
Payload: `{"trigger_full_sync": true}`

The function autonomously:
1. Calls Qualtrics API for each of the 8 survey IDs with `useLabels: true`
2. Polls until export is ready, downloads ZIP in memory
3. Extracts and parses CSV
4. Uses **row 1** (question label text) as column names when descriptive; falls back to row 0 (short name) for metadata columns
5. Sanitizes to snake_case, truncates to 60 chars, deduplicates with `_1`, `_2`
6. Upserts in chunks of 200 via `onConflict: 'responseid'`

### Mode B — Per-table upload (used by Python script)
Payload: `{"table_name": "<table>", "records": [...]}`

### Headers requeridos
```
Authorization: Bearer <UPLOAD_SECRET_TOKEN>
Content-Type: application/json
```

## Automation: pg_cron ✅

Active job: `sync-qualtrics-5min`, fires every 5 minutes.

```sql
-- Check status
select * from cron.job;

-- Check execution history
select * from cron.job_run_details order by start_time desc limit 10;

-- Remove cron
select cron.unschedule('sync-qualtrics-5min');
```

## Supabase Secrets (Edge Function)

| Secret | Purpose |
|---|---|
| `UPLOAD_SECRET_TOKEN` | Authenticates callers (Python script / pg_cron) |
| `SUPABASE_SERVICE_ROLE_KEY` | Allows upsert to PostgreSQL |
| `QUALTRICS_API_TOKEN` | Downloads survey exports from Qualtrics |
| `QUALTRICS_DATA_CENTER` | Qualtrics data center (e.g. `yourdatacenter.iad1`) |

## Survey IDs → Tables

| Survey ID | Table |
|---|---|
| SV_01cMwRVSLUkea2O | `billing_caseagent_survey` |
| SV_cT3pOd7JuEFj49M | `care_caseagent_survey_gpst_zonar` |
| SV_bx8nf9yQwWrDCU6 | `csat_survey_legacy_gpst_customers` |
| SV_cu3T7zZabenft8G | `csat_survey_legacy_zonar_customers` |
| SV_9mN6zmbtwrBQrd4 | `legacy_gps_trackit_customer_upsell_opportunity_survey` |
| SV_0e8MC0MLCGE15C6 | `legacy_zonar_customer_upsell_opportunity_survey` |
| SV_eOKq8bcQhNLaFdc | `closed_loss_survey` |
| SV_5bcQsvtTh41JaqW | `closed_win_survey` |

## Table Schema

- `id bigserial primary key`
- All other columns: `text`
- `UNIQUE constraint` on `responseid`
- Column names: descriptive labels from Qualtrics row 1 (question text), snake_case, max 60 chars

Recreate tables: run `recreate_tables_labels.sql` in SQL Editor.

## Environment Variables (`.env`) — Python script only

```
QUALTRICS_API_TOKEN=...
QUALTRICS_DATA_CENTER=your_datacenter.iad1
SURVEYS_OUTPUT_PATH=./qualtrics_surveys
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
UPLOAD_SECRET_TOKEN=...
```

## Manual run (backup)

```bash
python sync_qualtrics_to_supabase.py
```

## Dependencies (Python script)
- `requests`, `pandas`, `python-dotenv`, `zipfile`, `io`

## Computed Columns (GENERATED ALWAYS AS STORED)

These columns auto-calculate on every INSERT/UPDATE — no manual action needed.

### `care_caseagent_survey_gpst_zonar`
| Column | Logic |
|---|---|
| `calculated_score` | Score based on Q1 (`LIKE '%Yes%'` = resolved) and Q3 (Positive/Neutral/Negative Experience). See `add_score_column.sql` |

### `csat_survey_legacy_gpst_customers`
| Column | Logic |
|---|---|
| `nps_category` | Promoter (9-10) / Passive (7-8) / Detractor (0-6) from `how_likely_are_you_to_recommend_zonar` |
| `csat_satisfied` | 1 if Satisfied/Very Satisfied, 0 otherwise. From `how_would_you_rate_your_satisfaction_with_the_zonar` |

### `csat_survey_legacy_zonar_customers`
| Column | Logic |
|---|---|
| `nps_category` | Same as gpst |
| `csat_satisfied` | Same logic. Column: `how_would_you_rate_your_satisfaction_with_zonar` (no "the_") |

## Metrics Summary Cron Jobs (hourly)

| Cron Job | Target Table | Metrics |
|---|---|---|
| `update_csat_summary` | `csat_summary_csat_survey_legacy_gpst_customers` | csat_percentage, nps_score |
| `update_csat_summary_zonar` | `csat_summary_csat_survey_legacy_zonar_customers` | csat_percentage, nps_score |

Query summary:
```sql
SELECT * FROM csat_summary_csat_survey_legacy_gpst_customers;
SELECT * FROM csat_summary_csat_survey_legacy_zonar_customers;
```
