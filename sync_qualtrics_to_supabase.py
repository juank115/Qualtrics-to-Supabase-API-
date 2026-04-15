import os
import re
import time
import io
import zipfile
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# CONFIGURACION GENERAL
# ==========================================
def _require_env(name):
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Variable de entorno requerida no encontrada: {name}")
    return value

QUALTRICS_API_TOKEN    = _require_env("QUALTRICS_API_TOKEN")
QUALTRICS_DATA_CENTER  = _require_env("QUALTRICS_DATA_CENTER")
SUPABASE_URL           = _require_env("SUPABASE_URL")
UPLOAD_SECRET_TOKEN    = _require_env("UPLOAD_SECRET_TOKEN")

EDGE_FUNCTION_URL = os.environ.get("EDGE_FUNCTION_URL", f"{SUPABASE_URL}/functions/v1/upload-survey")
BASE_DOWNLOAD_PATH = os.environ.get("SURVEYS_OUTPUT_PATH", "./qualtrics_surveys")
CHUNK_SIZE = 200

# ==========================================
# 1. PARTE QUALTRICS (DESCARGA)
# ==========================================
def get_qualtrics_survey(dir_save_survey, survey_id):
    """Descarga el CSV de una encuesta Qualtrics y lo extrae en dir_save_survey.

    Usa el endpoint v3 export-responses (survey ID en la URL, no en el body).
    """
    file_format = "csv"
    base_url = f"https://{QUALTRICS_DATA_CENTER}.qualtrics.com/API/v3/surveys/{survey_id}/export-responses/"
    headers = {
        "content-type": "application/json",
        "x-api-token": QUALTRICS_API_TOKEN,
    }

    # Step 1: Iniciar exportacion
    payload = {"format": file_format, "useLabels": True}
    response = requests.post(base_url, json=payload, headers=headers, timeout=30)
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Error al iniciar export de Qualtrics para {survey_id} "
            f"(Status {response.status_code}): {response.text}"
        )
    result = response.json().get("result", {})
    progress_id = result.get("progressId")
    if not progress_id:
        raise RuntimeError(f"Respuesta inesperada de Qualtrics al iniciar export: {response.text}")

    # Step 2: Polling hasta que el export este listo (max 3 minutos)
    MAX_POLL_ATTEMPTS = 60
    for attempt in range(MAX_POLL_ATTEMPTS):
        check_response = requests.get(
            base_url + progress_id, headers=headers, timeout=30
        )
        if check_response.status_code != 200:
            raise RuntimeError(
                f"Error al verificar progreso de export (Status {check_response.status_code}): "
                f"{check_response.text}"
            )
        poll_result = check_response.json().get("result", {})
        progress = poll_result.get("percentComplete", 0)
        status = poll_result.get("status", "")

        if status == "complete" or progress >= 100:
            break

        time.sleep(3)
    else:
        raise RuntimeError(
            f"Export de Qualtrics no completo tras {MAX_POLL_ATTEMPTS} intentos ({survey_id})"
        )

    # Step 3: Descargar archivo ZIP
    download_response = requests.get(
        base_url + progress_id + "/file", headers=headers, stream=True, timeout=120
    )
    if download_response.status_code != 200:
        raise RuntimeError(
            f"Error al descargar archivo (Status {download_response.status_code}): "
            f"{download_response.text}"
        )

    # Step 4: Descomprimir
    zipfile.ZipFile(io.BytesIO(download_response.content)).extractall(dir_save_survey)
    print(f"  OK Descargado y descomprimido: {survey_id}")


# ==========================================
# 2. PARTE SUPABASE (SUBIDA)
# ==========================================
def sanitize_name(name, max_len=60):
    name = name.lower()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")[:max_len]


def send_chunk(table_name, records, chunk_index, retries=3):
    headers = {
        "Authorization": f"Bearer {UPLOAD_SECRET_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "table_name": table_name,
        "records": records,
    }

    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                EDGE_FUNCTION_URL, json=payload, headers=headers, timeout=60
            )
            if response.status_code in (200, 201):
                print(f"    Lote {chunk_index}: {len(records)} filas enviadas a Edge Function.")
                return
            else:
                raise RuntimeError(
                    f"Lote {chunk_index}: Error Edge Function "
                    f"(Status {response.status_code}): {response.text}"
                )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < retries:
                print(f"    Lote {chunk_index}: Error de red, reintento {attempt}/{retries}...")
                time.sleep(3)
            else:
                raise RuntimeError(
                    f"Lote {chunk_index}: Fallo tras {retries} intentos. {e}"
                )


def send_qualtrics_to_supabase(path):
    for filename in os.listdir(path):
        if not filename.endswith(".csv"):
            continue

        file_path = os.path.join(path, filename)
        table_name = sanitize_name(filename.replace(".csv", ""))

        # Leer todas las filas sin header para combinar row0 y row1
        raw = pd.read_csv(file_path, header=None, dtype=str)
        row0 = raw.iloc[0].tolist()
        row1 = raw.iloc[1].tolist()

        # Usar row1 (question label) si es descriptivo; si no, usar row0 (short name)
        combined = []
        for r0, r1 in zip(row0, row1):
            use_label = r1 and not str(r1).startswith("{") and r1 != r0
            combined.append(r1 if use_label else r0)

        sanitized = [sanitize_name(h) for h in combined]
        seen = {}
        final_cols = []
        for c in sanitized:
            if c in seen:
                seen[c] += 1
                final_cols.append(f"{c}_{seen[c]}")
            else:
                seen[c] = 0
                final_cols.append(c)

        # Data empieza en row 3 (saltamos row0=headers, row1=labels, row2=importIDs)
        df = raw.iloc[3:].copy()
        df.columns = final_cols
        df = df.astype(object).where(pd.notnull(df), None)

        records = df.to_dict(orient="records")
        total = len(records)
        print(f"  Subiendo '{filename}' a la tabla '{table_name}' ({total} filas totales)...")

        for i in range(0, total, CHUNK_SIZE):
            chunk = records[i : i + CHUNK_SIZE]
            send_chunk(table_name, chunk, chunk_index=(i // CHUNK_SIZE) + 1)


# ==========================================
# EJECUCION PRINCIPAL
# ==========================================
if __name__ == "__main__":

    # Asegurar que el directorio de salida existe
    os.makedirs(BASE_DOWNLOAD_PATH, exist_ok=True)

    # Lote de IDs de encuesta a procesar
    # Add your Qualtrics survey IDs here, e.g.: "SV_xxxxxxxxxxxx"
    survey_ids = [
    ]

    print("\n--- 1. INICIANDO DESCARGA DESDE QUALTRICS ---")
    for survey_id in survey_ids:
        print(f"Consultando survey ID: {survey_id}...")
        get_qualtrics_survey(dir_save_survey=BASE_DOWNLOAD_PATH, survey_id=survey_id)

    print("\n--- 2. INICIANDO SUBIDA HACIA SUPABASE ---")
    send_qualtrics_to_supabase(BASE_DOWNLOAD_PATH)

    print("\n--- PROCESO COMPLETADO EXITOSAMENTE ---")
