# 📊 Qualtrics → Supabase Pipeline

A fully automated, cloud-native data pipeline that synchronizes customer survey data from **Qualtrics** into a **Supabase** PostgreSQL database — with zero manual intervention required.

<p align="center">
  <img src="https://img.shields.io/badge/Qualtrics-API-00A0DF?style=for-the-badge&logo=qualtrics&logoColor=white" />
  <img src="https://img.shields.io/badge/Supabase-Edge_Functions-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-pg__cron-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/Python-Backup_Script-3776AB?style=for-the-badge&logo=python&logoColor=white" />
</p>

---

## 🧩 Overview

This project builds an end-to-end integration that:

1. **Downloads** survey response data from 8 Qualtrics surveys via the Qualtrics API
2. **Processes** the exported CSV files in-memory (parsing, label extraction, sanitization)
3. **Upserts** the data into Supabase PostgreSQL tables with idempotent operations
4. **Runs autonomously** every 5 minutes via `pg_cron` — no local machine needed

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        SUPABASE CLOUD                          │
│                                                                 │
│   pg_cron (*/5 * * * *)                                        │
│       │                                                         │
│       ▼                                                         │
│   Edge Function: upload-survey                                  │
│       │                                                         │
│       ├──► Qualtrics API (export with labels)                   │
│       │       │                                                 │
│       │       ▼                                                 │
│       │   ZIP in memory → CSV parsed → columns sanitized        │
│       │                                                         │
│       ▼                                                         │
│   PostgreSQL (upsert on responseid)                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────┐
│          MANUAL BACKUP               │
│                                      │
│   python sync_qualtrics_to_supabase  │
│       │                              │
│       ▼                              │
│   POST → Edge Function → PostgreSQL  │
└──────────────────────────────────────┘
```

---


## ⚙️ How It Works

### Edge Function (`upload-survey`)

The Supabase Edge Function supports two modes:

| Mode | Trigger | Payload | Use Case |
|---|---|---|---|
| **Full Sync** | `pg_cron` (every 5 min) | `{"trigger_full_sync": true}` | Automated daily operations |
| **Per-Table Upload** | Python script | `{"table_name": "...", "records": [...]}` | Manual backup / ad-hoc sync |

### Data Processing Pipeline

1. **Export** — Calls Qualtrics API with `useLabels: true` to get human-readable values (e.g. "Very Satisfied" instead of "1")
2. **Download** — Polls until export is ready, downloads ZIP archive
3. **Parse** — Extracts CSV in memory, uses **row 1** (question label text) as column names for descriptive headers
4. **Sanitize** — Converts to `snake_case`, truncates to 60 chars, deduplicates (`_1`, `_2`)
5. **Upsert** — Inserts new records or updates existing ones (matched by `responseid`) in chunks of 200

## 🚀 Getting Started

### Prerequisites

- Python 3.8+
- A [Qualtrics](https://www.qualtrics.com/) account with API access
- A [Supabase](https://supabase.com/) project

### 1. Clone the repository

```bash
git clone https://github.com/juank115/Qualtrics-to-Supabase-API-.git
cd Qualtrics-to-Supabase-API-
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit the `.env` file with your credentials:

```env
QUALTRICS_API_TOKEN=your_qualtrics_api_token
QUALTRICS_DATA_CENTER=your_datacenter.iad1
SURVEYS_OUTPUT_PATH=./qualtrics_surveys
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
UPLOAD_SECRET_TOKEN=your_upload_secret_token
```

### 4. Set up the database

Create the survey tables in your Supabase SQL Editor. Each table should follow this schema:

- `id bigserial primary key`
- All survey columns as `text`
- `UNIQUE constraint` on `responseid`

> The Edge Function auto-creates columns based on the Qualtrics CSV headers using descriptive labels (snake_case, max 60 chars).

### 5. Deploy the Edge Function

Deploy `upload-survey` to your Supabase project and configure the following secrets:

| Secret | Description |
|---|---|
| `UPLOAD_SECRET_TOKEN` | Authenticates callers (pg_cron / Python script) |
| `SUPABASE_SERVICE_ROLE_KEY` | Allows upsert operations to PostgreSQL |
| `QUALTRICS_API_TOKEN` | Downloads survey exports from Qualtrics |
| `QUALTRICS_DATA_CENTER` | Your Qualtrics data center subdomain |

### 6. Enable automation

Enable the `pg_cron` and `pg_net` extensions in **Dashboard → Database → Extensions**, then schedule the sync:

```sql
select cron.schedule(
  'sync-qualtrics-5min',
  '*/5 * * * *',
  $$
  select net.http_post(
    url     := '<YOUR_SUPABASE_URL>/functions/v1/upload-survey',
    headers := '{"Authorization": "Bearer <YOUR_TOKEN>", "Content-Type": "application/json"}'::jsonb,
    body    := '{"trigger_full_sync": true}'::jsonb
  );
  $$
);
```

### 7. Manual sync (backup)

```bash
python sync_qualtrics_to_supabase.py
```

---

## 📁 Project Structure

```
├── sync_qualtrics_to_supabase.py    # Python backup script for manual sync
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment variable template
├── README.md                        # This file
├── docs/
│   ├── ROADMAP_SUPABASE.md          # Full integration roadmap & history
│   ├── CONTEXT.md                   # Project context & technical decisions
│   └── CLAUDE.md                    # Quick technical reference
└── .gitignore
```

---

## 🔒 Security

| Practice | Implementation |
|---|---|
| Credentials out of code | `.env` locally + Supabase Secrets in the cloud |
| DB credentials never in Python | Python only knows `UPLOAD_SECRET_TOKEN` |
| Idempotent upserts | Running N times produces the same result |
| No frontend exposure | Everything runs internally in Supabase |
| Retry logic | 3 attempts with 3s delay between each |

---

## 🛠️ Tech Stack

| Technology | Role |
|---|---|
| **Qualtrics API** | Survey data source |
| **Supabase Edge Functions** (Deno) | Serverless data processing |
| **Supabase PostgreSQL** | Data storage with computed columns |
| **pg_cron** | Automated scheduling (every 5 min) |
| **pg_net** | HTTP requests from within PostgreSQL |
| **Python** | Manual backup script |
| **pandas** | CSV parsing and data transformation |
| **fflate** | ZIP decompression in Edge Function |

---

## 📄 License

This project is for internal use. Feel free to fork and adapt for your own Qualtrics + Supabase integration.
