from pyairtable import Api
import config

def get_airtable_table(table_name: str = "Live_Snapshots"):
    api = Api(config.AIRTABLE_TOKEN)
    return api.table(config.AIRTABLE_BASE_ID, table_name)

def upsert_live_records(records: list[dict], table_name: str = "Live_Snapshots"):
    """
    Batch upsert records into Airtable.
    Airtable allows max 10 records per request.
    """
    table = get_airtable_table(table_name)
    
    # Airtable batch size limit is 10
    for i in range(0, len(records), 10):
        batch = records[i:i+10]
        table.batch_create(batch)
