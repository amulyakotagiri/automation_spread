"""
BigQuery helper – live snapshots + static metrics
"""
import os
import json
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.oauth2.service_account import Credentials
import config

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "").strip()
DATASET_ID = os.environ.get("BQ_DATASET_ID", "market_depth").strip()
SCOPES = ["https://www.googleapis.com/auth/bigquery"]

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
    creds_json = os.environ.get("GOOGLE_CREDENTIALS", "").strip()
    if creds_json:
        creds_dict = json.loads(creds_json)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        source = "GOOGLE_CREDENTIALS secret"
    else:
        credentials = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
        )
        source = f"file {config.GOOGLE_CREDENTIALS_FILE}"

    project = PROJECT_ID or credentials.project_id
    print(
        f"[BIGQUERY] Auth from {source} | service account: "
        f"{credentials.service_account_email} | project: {project}",
        flush=True,
    )
    return bigquery.Client(project=project, credentials=credentials)


def _ensure_table(client: bigquery.Client, table_id: str, schema: list,
                  partition_field: str = None) -> str:
    dataset_ref = bigquery.DatasetReference(client.project, DATASET_ID)

    # Only treat "not found" as missing; permission errors will surface as-is
    try:
        client.get_dataset(dataset_ref)
    except NotFound:
        print(f"[BIGQUERY] Creating dataset {DATASET_ID}...", flush=True)
        client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)

    table_ref = dataset_ref.table(table_id)
    try:
        client.get_table(table_ref)
    except NotFound:
        print(f"[BIGQUERY] Creating table {table_id}...", flush=True)
        table = bigquery.Table(table_ref, schema=schema)
        if partition_field:
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field=partition_field,
            )
        client.create_table(table, exists_ok=True)

    return f"{client.project}.{DATASET_ID}.{table_id}"


def _serialize_timestamps(records: list[dict], keys: tuple) -> list[dict]:
    rows = []
    for r in records:
        row = dict(r)
        for key in keys:
            ts = row.get(key)
            if hasattr(ts, "isoformat"):
                row[key] = ts.isoformat()
        rows.append(row)
    return rows


def _append_rows(client, full_table_id, rows, schema):
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    load_job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    load_job.result()


def write_snapshot_records(records: list[dict]):
    """Append live snapshot rows."""
    if not records:
        print("[BIGQUERY] No snapshot records to write.", flush=True)
        return

    client = _get_client()
    full_table_id = _ensure_table(client, "snapshots", SNAPSHOT_SCHEMA, "Snapshot_Time")
    rows = _serialize_timestamps(records, ("Snapshot_Time",))
    _append_rows(client, full_table_id, rows, SNAPSHOT_SCHEMA)
    print(f"[BIGQUERY] Appended {len(rows)} snapshot rows → {full_table_id}", flush=True)


def write_static_metrics(records: list[dict]):
    """Append latest static metrics (one row per symbol per run)."""
    if not records:
        print("[BIGQUERY] No static metrics to write.", flush=True)
        return

    client = _get_client()
    full_table_id = _ensure_table(client, "static_metrics", STATIC_SCHEMA, "updated_at")
    rows = _serialize_timestamps(records, ("last_history_update", "updated_at"))
    _append_rows(client, full_table_id, rows, STATIC_SCHEMA)
    print(f"[BIGQUERY] Appended {len(rows)} static metric rows → {full_table_id}", flush=True)
