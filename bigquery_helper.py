"""
BigQuery helper – live snapshots + static metrics
"""
import os
import json
from datetime import datetime
from google.cloud import bigquery
from google.oauth2.service_account import Credentials
import config

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "").strip()
DATASET_ID = os.environ.get("BQ_DATASET_ID", "market_depth").strip()

# ---------- Schemas ----------
SNAPSHOT_SCHEMA = [
    bigquery.SchemaField("Snapshot_Time", "TIMESTAMP"),
    bigquery.SchemaField("Session", "STRING"),
    bigquery.SchemaField("Category", "STRING"),
    bigquery.SchemaField("SYMBOL", "STRING"),
    bigquery.SchemaField("security_id", "STRING"),
    bigquery.SchemaField("Bid", "FLOAT64"),
    bigquery.SchemaField("Ask", "FLOAT64"),
    bigquery.SchemaField("Spread", "FLOAT64"),
    bigquery.SchemaField("LTP", "FLOAT64"),
    bigquery.SchemaField("Volume", "INT64"),
]

STATIC_SCHEMA = [
    bigquery.SchemaField("SYMBOL", "STRING"),
    bigquery.SchemaField("security_id", "STRING"),
    bigquery.SchemaField("Category", "STRING"),
    bigquery.SchemaField("last_history_update", "TIMESTAMP"),
    bigquery.SchemaField("volatility_6m", "FLOAT64"),
    bigquery.SchemaField("atr_14", "FLOAT64"),
    bigquery.SchemaField("atr_pct_of_price", "FLOAT64"),
    bigquery.SchemaField("avg_volume_6m", "FLOAT64"),
    bigquery.SchemaField("updated_at", "TIMESTAMP"),
]


def _get_client() -> bigquery.Client:
    # Prefer the JSON secret (GitHub Actions style)
    creds_json = os.environ.get("GOOGLE_CREDENTIALS", "").strip()
    if creds_json:
        creds_dict = json.loads(creds_json)
        credentials = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/bigquery"]
        )
        project = PROJECT_ID or creds_dict.get("project_id")
        return bigquery.Client(project=project, credentials=credentials)

    # Fallback to file (local development)
    credentials = Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    project = PROJECT_ID or credentials.project_id
    return bigquery.Client(project=project, credentials=credentials)


def _ensure_table(client: bigquery.Client, table_id: str, schema: list, partition_field: str = None) -> str:
    dataset_ref = bigquery.DatasetReference(client.project, DATASET_ID)
    try:
        client.get_dataset(dataset_ref)
    except Exception:
        print(f"[BIGQUERY] Creating dataset {DATASET_ID}...", flush=True)
        client.create_dataset(bigquery.Dataset(dataset_ref))

    table_ref = dataset_ref.table(table_id)
    try:
        client.get_table(table_ref)
    except Exception:
        print(f"[BIGQUERY] Creating table {table_id}...", flush=True)
        table = bigquery.Table(table_ref, schema=schema)
        if partition_field:
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field=partition_field
            )
        client.create_table(table)

    return f"{client.project}.{DATASET_ID}.{table_id}"


def write_snapshot_records(records: list[dict]):
    """Append live snapshot rows."""
    if not records:
        print("[BIGQUERY] No snapshot records to write.", flush=True)
        return

    client = _get_client()
    full_table_id = _ensure_table(client, "snapshots", SNAPSHOT_SCHEMA, "Snapshot_Time")

    rows = []
    for r in records:
        row = dict(r)
        ts = row.get("Snapshot_Time")
        if hasattr(ts, "isoformat"):
            row["Snapshot_Time"] = ts.isoformat()
        rows.append(row)

    job_config = bigquery.LoadJobConfig(
        schema=SNAPSHOT_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    load_job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    load_job.result()
    print(f"[BIGQUERY] Appended {len(rows)} snapshot rows → {full_table_id}", flush=True)


def write_static_metrics(records: list[dict]):
    """Append latest static metrics (one row per symbol per run)."""
    if not records:
        print("[BIGQUERY] No static metrics to write.", flush=True)
        return

    client = _get_client()
    full_table_id = _ensure_table(client, "static_metrics", STATIC_SCHEMA, "updated_at")

    rows = []
    for r in records:
        row = dict(r)
        for key in ("last_history_update", "updated_at"):
            ts = row.get(key)
            if hasattr(ts, "isoformat"):
                row[key] = ts.isoformat()
        rows.append(row)

    job_config = bigquery.LoadJobConfig(
        schema=STATIC_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    load_job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    load_job.result()
    print(f"[BIGQUERY] Appended {len(rows)} static metric rows → {full_table_id}", flush=True)
