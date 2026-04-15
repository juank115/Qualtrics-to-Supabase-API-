/**
 * Edge Function: upload-survey
 *
 * Supports two modes:
 *   A) Full sync (pg_cron trigger): { "trigger_full_sync": true }
 *   B) Per-table upload (Python script): { "table_name": "...", "records": [...] }
 *
 * Authentication:
 *   - Primary:   Authorization: Bearer <UPLOAD_SECRET_TOKEN>  (custom shared secret)
 *   - Optional:  JWT verification can be enabled via supabase/config.toml
 *                by setting verify_jwt = true under [functions.upload-survey].
 *                When enabled, Supabase validates the JWT automatically before
 *                this handler runs — add it as a second layer for production.
 *
 * NOTE: This file is a reference template. The deployed function lives in the
 * Supabase Dashboard. To deploy from CLI: `supabase functions deploy upload-survey`
 */

import { createClient } from "jsr:@supabase/supabase-js@2";

// ---------- auth ----------
function authenticate(req: Request): void {
  const authHeader = req.headers.get("Authorization") ?? "";
  const token = authHeader.replace(/^Bearer\s+/i, "");
  const expected = Deno.env.get("UPLOAD_SECRET_TOKEN");
  if (!expected || token !== expected) {
    throw new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }
}

// ---------- helpers ----------
function sanitizeName(name: string, maxLen = 60): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, maxLen);
}

async function sleep(ms: number) {
  await new Promise((r) => setTimeout(r, ms));
}

// ---------- Qualtrics download ----------
async function downloadSurvey(surveyId: string): Promise<string[][]> {
  const token = Deno.env.get("QUALTRICS_API_TOKEN")!;
  const dc    = Deno.env.get("QUALTRICS_DATA_CENTER")!;
  const base  = `https://${dc}.qualtrics.com/API/v3/surveys/${surveyId}/export-responses/`;
  const headers = { "content-type": "application/json", "x-api-token": token };

  // Start export
  const startRes = await fetch(base, {
    method: "POST",
    headers,
    body: JSON.stringify({ format: "csv", useLabels: true }),
  });
  if (!startRes.ok) throw new Error(`Qualtrics export start failed: ${await startRes.text()}`);
  const { result: { progressId } } = await startRes.json();

  // Poll until ready (max 3 min)
  for (let i = 0; i < 60; i++) {
    const pollRes = await fetch(base + progressId, { headers });
    if (!pollRes.ok) throw new Error(`Qualtrics poll failed: ${await pollRes.text()}`);
    const { result } = await pollRes.json();
    if (result.status === "complete" || result.percentComplete >= 100) break;
    await sleep(3000);
    if (i === 59) throw new Error(`Export timed out for survey ${surveyId}`);
  }

  // Download ZIP
  const dlRes = await fetch(base + progressId + "/file", { headers });
  if (!dlRes.ok) throw new Error(`Qualtrics download failed: ${await dlRes.text()}`);

  // Decompress in memory using fflate
  const { unzipSync } = await import("https://esm.sh/fflate@0.8.2");
  const zipBuffer = new Uint8Array(await dlRes.arrayBuffer());
  const files = unzipSync(zipBuffer);
  const csvBytes = Object.values(files)[0];
  const csv = new TextDecoder().decode(csvBytes);

  // Parse CSV rows
  return csv.split("\n").map((line) =>
    line.split(",").map((cell) => cell.replace(/^"|"$/g, "").trim())
  );
}

// ---------- Supabase upsert ----------
async function upsertRecords(
  supabase: ReturnType<typeof createClient>,
  tableName: string,
  records: Record<string, unknown>[],
) {
  const CHUNK = 200;
  for (let i = 0; i < records.length; i += CHUNK) {
    const chunk = records.slice(i, i + CHUNK);
    const { error } = await supabase
      .from(tableName)
      .upsert(chunk, { onConflict: "responseid" });
    if (error) throw new Error(`Upsert error on ${tableName}: ${error.message}`);
  }
}

// ---------- main ----------
Deno.serve(async (req: Request) => {
  try {
    // Reject non-POST requests
    if (req.method !== "POST") {
      return new Response(JSON.stringify({ error: "Method not allowed" }), {
        status: 405,
        headers: { "Content-Type": "application/json" },
      });
    }

    // Authenticate
    try { authenticate(req); } catch (res) { return res as Response; }

    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );

    const body = await req.json();

    // Mode A: Full sync (triggered by pg_cron)
    if (body.trigger_full_sync) {
      const SURVEY_IDS: string[] = [
        // Add your Qualtrics survey IDs here
        // "SV_xxxxxxxxxxxx",
      ];

      for (const surveyId of SURVEY_IDS) {
        const rows = await downloadSurvey(surveyId);
        const row0 = rows[0];
        const row1 = rows[1];

        // Build column names from row1 (labels) or row0 (short names)
        const combined = row0.map((r0, i) => {
          const r1 = row1[i] ?? "";
          return r1 && !r1.startsWith("{") && r1 !== r0 ? r1 : r0;
        });

        const seen: Record<string, number> = {};
        const cols = combined.map((c) => {
          const s = sanitizeName(c);
          if (s in seen) { seen[s]++; return `${s}_${seen[s]}`; }
          seen[s] = 0; return s;
        });

        // Data starts at row 3 (skip row0=names, row1=labels, row2=importIDs)
        const records = rows.slice(3).map((row) =>
          Object.fromEntries(cols.map((col, i) => [col, row[i] ?? null]))
        );

        const tableName = sanitizeName(surveyId);
        await upsertRecords(supabase, tableName, records);
      }

      return new Response(JSON.stringify({ ok: true, mode: "full_sync" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    // Mode B: Per-table upload (from Python script)
    if (body.table_name && Array.isArray(body.records)) {
      await upsertRecords(supabase, body.table_name, body.records);
      return new Response(
        JSON.stringify({ ok: true, mode: "per_table", table: body.table_name }),
        { headers: { "Content-Type": "application/json" } },
      );
    }

    return new Response(JSON.stringify({ error: "Invalid payload" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
});
