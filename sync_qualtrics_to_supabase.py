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
SUPABASE_URL = os.environ.get("SUPABASE_URL")
EDGE_FUNCTION_URL = os.environ.get("EDGE_FUNCTION_URL", f"{SUPABASE_URL}/functions/v1/upload-survey")
UPLOAD_SECRET_TOKEN = os.environ.get("UPLOAD_SECRET_TOKEN")

QUALTRICS_API_TOKEN = os.environ["QUALTRICS_API_TOKEN"]
QUALTRICS_DATA_CENTER = os.environ["QUALTRICS_DATA_CENTER"]

BASE_DOWNLOAD_PATH = os.environ.get("SURVEYS_OUTPUT_PATH", "./qualtrics_surveys")
CHUNK_SIZE = 200

# ==========================================
# 1. PARTE QUALTRICS (DESCARGA)
# ==========================================
def get_qualtrics_survey(dir_save_survey, survey_id):
    """ automatically query the qualtrics survey data
    guide https://community.alteryx.com/t5/Alteryx-Designer-Discussions/Python-Tool-Downloading-Qualtrics-Survey-Data-using-Python-API/td-p/304898 """
    
    file_format = "csv"
    request_check_progress = 0
    progress_status = "in progress"
    base_url = f"https://{QUALTRICS_DATA_CENTER}.qualtrics.com/API/v3/responseexports/"
    headers = {
        "content-type": "application/json",
        "x-api-token": QUALTRICS_API_TOKEN,
    }

    # Step 1: Creating Data Export
    download_request_payload = '{"format":"' + file_format + '","surveyId":"' + survey_id + '","useLabels":true}'
    download_request_response = requests.request("POST", base_url, data=download_request_payload, headers=headers)
    progress_id = download_request_response.json()["result"]["id"]

    # Step 2: Checking on Data Export Progress and waiting until export is ready
    while request_check_progress < 100 and progress_status != "complete":
        request_check_url = base_url + progress_id
        request_check_response = requests.request("GET", request_check_url, headers=headers)
        request_check_progress = request_check_response.json()["result"]["percentComplete"]

    # Step 3: Downloading file
    request_download_url = base_url + progress_id + '/file'
    request_download = requests.request("GET", request_download_url, headers=headers, stream=True)

    # Step 4: Unzipping the file
    zipfile.ZipFile(io.BytesIO(request_download.content)).extractall(dir_save_survey)
    print(f'  OK Descargado y descomprimido: {survey_id}')

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
    
    # La Edge Function espera exactamente este payload
    payload = {
        "table_name": table_name,
        "records": records
    }
    
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(EDGE_FUNCTION_URL, json=payload, headers=headers, timeout=60)
            if response.status_code in (200, 201):
                print(f"    Lote {chunk_index}: {len(records)} filas enviadas a Edge Function.")
                return
            else:
                raise RuntimeError(f"Lote {chunk_index}: Error Edge Function (Status {response.status_code}): {response.text}")
        except requests.exceptions.ConnectionError as e:
            if attempt < retries:
                print(f"    Lote {chunk_index}: Conexion caida, reintento {attempt}/{retries}...")
                time.sleep(3)
            else:
                raise RuntimeError(f"Lote {chunk_index}: Fallo tras {retries} intentos. {e}")

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

        # Usar row1 (question label) si es descriptivo, si no usar row0 (short name)
        combined = []
        for r0, r1 in zip(row0, row1):
            use_label = r1 and not str(r1).startswith('{') and r1 != r0
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
            chunk = records[i:i + CHUNK_SIZE]
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
