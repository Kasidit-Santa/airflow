from airflow import DAG
from airflow.decorators import task
from airflow.utils.dates import days_ago
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.utils.task_group import TaskGroup
from airflow.exceptions import AirflowSkipException
from airflow.utils.trigger_rule import TriggerRule

import hashlib
import logging
import requests
import pandas as pd
import io
import time
import re
import os
from datetime import timedelta, datetime

# --- NEW IMPORTS FOR SENTENCE TRANSFORMERS & CHROMA DB ---
from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.utils import embedding_functions

# --- CKAN API Configuration ---
API_KEY = "API-KEY"  # Ensure this is correct and secure
PACKAGE_ID = "8a956917-436d-4afd-a2d4-59e4dd8e906e"
METADATA_URL = f"https://opend.data.go.th/get-ckan/package_show?id={PACKAGE_ID}"
MINIO_BUCKET_NAME = "airflow-bucket-cov"

# --- ChromaDB and Embedding Model Configuration ---
CHROMA_HOST = "host.docker.internal"
CHROMA_PORT = 8000
CHROMA_COLLECTION_NAME = "covid_data_collection_multi_vector"
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

with DAG(
    dag_id="etl-cov-rag-chroma-multi-vector-v3h", # <--- Changed DAG ID
    start_date=days_ago(1),
    schedule_interval=timedelta(days=1),
    catchup=False,
    tags=["etl", "scraping", "ckan", "covid", "rag", "embedding", "chromadb", "sentence-transformers", "multi-vector"],
) as dag:

    log = logging.getLogger(__name__)

    # --- ETL TASKS (Defined First, in logical order) ---

    @task()
    def fetch_ckan_metadata():
        """
        Fetches metadata from the CKAN API to find available resources.
        """
        headers = {"api-key": API_KEY}
        response = requests.get(METADATA_URL, headers=headers)
        response.raise_for_status()
        metadata = response.json()["result"]["resources"]
        log.info(f"Fetched CKAN metadata with {len(metadata)} resources.")
        return metadata

    @task()
    def get_csv_urls_from_ckan(metadata):
        """
        Identifies CSV URLs from CKAN metadata.
        It strictly prioritizes files that need to be downloaded/re-downloaded from CKAN
        (i.e., never successfully downloaded to S3, or explicitly failed at S3 stage).
        """
        log.info("Fetching list of already processed raw resource IDs and their statuses from the database for download decision.")
        pg_hook = PostgresHook(postgres_conn_id="postgres_localhost")
        
        try:
            df_logs = pg_hook.get_pandas_df("SELECT resource_id, status FROM file_logs")
            resource_status_map = dict(zip(df_logs["resource_id"], df_logs["status"]))
        except Exception as e:
            log.warning(f"Could not retrieve file_logs. Assuming no previous processing. Error: {e}")
            resource_status_map = {}

        csv_urls_to_process = []
        for resource in metadata:
            resource_id = resource["id"]
            resource_format = resource.get("format", "").lower()
            resource_url = resource["url"]

            if resource_format == "csv":
                current_status = resource_status_map.get(resource_id)

                if current_status is None or current_status == "failed_s3":
                    log.info(f"Resource {resource_id} (status: {current_status}) requires raw download from CKAN.")
                    csv_urls_to_process.append(resource_url)
                else:
                    log.info(f"Resource {resource_id} (status: {current_status}) does NOT require raw download from CKAN. Skipping.")
                    continue

            else:
                log.info(f"Resource {resource_id} is not CSV format ({resource_format}). Skipping.")

        if not csv_urls_to_process:
            log.info("No new CSV resources found in CKAN metadata requiring raw download. Returning empty list.")
            return [] 

        log.info(f"Found {len(csv_urls_to_process)} new CSV URLs to download/re-attempt raw processing.")
        return csv_urls_to_process


    @task(
        retries=3,
        retry_delay=timedelta(seconds=10),
    )
    def extract_transform_and_store_single_file(url: str):
        """
        Extracts a single CSV, transforms its delimiter (to pipe), and stores it in MinIO.
        Returns information about the stored file and its new status.
        """
        s3_hook = S3Hook(aws_conn_id="minio_conn")
        pg_hook = PostgresHook(postgres_conn_id="postgres_localhost") # Used for logging status
        log = logging.getLogger(__name__)

        resource_id = url.split("/")[-3]
        transformed_key = None # Initialize to None

        log.info(f"Attempting to extract and transform: {url} (Resource ID: {resource_id})")
        
        try:
            response = requests.get(url)
            response.raise_for_status()

            csv_text = response.content.decode("utf-8")
            if csv_text.startswith('\ufeff'):
                csv_text = csv_text.lstrip('\ufeff') # Remove BOM if present

            original_filename = url.split('/')[-1]
            extract_folder_name = original_filename.rsplit('.', 1)[0]
            transformed_key = f"results/pipe/{extract_folder_name}/{resource_id}.csv"

            df = pd.read_csv(io.StringIO(csv_text))
            buffer = io.StringIO()
            df.to_csv(buffer, sep="|", index=False) # Transform delimiter
            buffer.seek(0)

            # Upload transformed file to MinIO
            s3_hook.load_string(
                string_data=buffer.getvalue(),
                key=transformed_key,
                bucket_name=MINIO_BUCKET_NAME,
                replace=True
            )
            log.info(f"Transformed and uploaded file to s3://{MINIO_BUCKET_NAME}/{transformed_key}")
            
            status = "downloaded" # Successfully downloaded from CKAN and uploaded to S3
            
        except Exception as e:
            log.error(f"Error extracting, transforming or storing {url} in MinIO: {e}")
            status = "failed_s3" # Mark as failed at S3 stage
            raise # Re-raise to fail the task and allow Airflow to retry

        finally:
            # Log the status of this raw file processing attempt
            conn = None
            try:
                conn = pg_hook.get_conn()
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO file_logs (resource_id, s3_key, status, processed_at, last_attempt_at)
                    VALUES (%s, %s, %s, NOW(), NOW())
                    ON CONFLICT (resource_id) DO UPDATE
                    SET s3_key = EXCLUDED.s3_key,
                        status = EXCLUDED.status,
                        processed_at = COALESCE(file_logs.processed_at, NOW()), -- Keep original processed_at if exists
                        last_attempt_at = NOW();
                    """,
                    (resource_id, transformed_key, status)
                )
                conn.commit()
                log.info(f"Logged status for resource {resource_id}: {status}")
            except Exception as log_err:
                log.error(f"Error logging status to file_logs for {resource_id}: {log_err}")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    cur.close()
                    conn.close()
        
        # This return value is passed to downstream mapped tasks
        return {"resource_id": resource_id, "s3_key": transformed_key, "status": status}

    @task()
    def filter_successful_s3_uploads(processed_files_info_list: list):
        """
        Filters successfully uploaded S3 files that need to be loaded into Postgres.
        Raises AirflowSkipException if no files are ready for PostgreSQL load.
        """
        files_for_postgres_load = []
        pg_hook = PostgresHook(postgres_conn_id="postgres_localhost")
        
        try:
            df_logs = pg_hook.get_pandas_df("SELECT resource_id, status FROM file_logs")
            resource_status_map = dict(zip(df_logs["resource_id"], df_logs["status"]))
        except Exception as e:
            log.warning(f"Could not retrieve file_logs for filtering. Assuming all need processing. Error: {e}")
            resource_status_map = {}

        for info in processed_files_info_list:
            resource_id = info["resource_id"]
            current_status = resource_status_map.get(resource_id)

            if info.get("status") == "downloaded" or \
               current_status in ["failed_pg", "failed_chroma", "skipped_no_valid_ids"]:
                files_for_postgres_load.append(info)
            else:
                log.info(f"Skipping {resource_id} for PostgreSQL pipeline: Status is '{current_status}'.")


        if not files_for_postgres_load:
            log.info("No files ready for PostgreSQL loading. Skipping.")
            raise AirflowSkipException("No files for PostgreSQL load.")
        
        log.info(f"Found {len(files_for_postgres_load)} files for PostgreSQL loading.")
        return files_for_postgres_load

    @task()
    def create_covid_data_table():
        """
        Ensures the 'th_covid_data', 'file_logs', and 'covid_data_embeddings' tables exist in PostgreSQL.
        """
        pg_hook = PostgresHook(postgres_conn_id="postgres_localhost")
        
        # Create th_covid_data table
        sql_covid_data = """
        CREATE TABLE IF NOT EXISTS th_covid_data (
            _id INTEGER PRIMARY KEY,
            resource_id VARCHAR(255),
            announce_date TEXT,
            notified_date TEXT,
            sex TEXT,
            age INTEGER,
            unit TEXT,
            nationality TEXT,
            province_of_isolation TEXT,
            risk TEXT,
            province_of_onset TEXT,
            district_of_onset TEXT,
            loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_for_embedding BOOLEAN DEFAULT FALSE
        );
        """
        pg_hook.run(sql_covid_data)
        log.info("Ensured 'th_covid_data' table exists in PostgreSQL.")

        # Create file_logs table
        sql_file_logs = """
        CREATE TABLE IF NOT EXISTS file_logs (
            resource_id VARCHAR(255) PRIMARY KEY,
            s3_key TEXT,
            status VARCHAR(50),
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_attempt_at TIMESTAMP
        );
        """
        pg_hook.run(sql_file_logs)
        log.info("Ensured 'file_logs' table exists in PostgreSQL.")

        # # --- NEW: Create covid_data_embeddings table ---
        # sql_embeddings_table = """
        # CREATE TABLE IF NOT EXISTS covid_data_embeddings (
        #     embedding_id VARCHAR(255) PRIMARY KEY,
        #     record_id INTEGER REFERENCES th_covid_data(_id) ON DELETE CASCADE,
        #     embedding_vector TEXT,  -- Storing as text, consider vector extension for production
        #     embedding_type VARCHAR(50), -- 'location', 'risk', 'demographics'
        #     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        # );
        # """
        # pg_hook.run(sql_embeddings_table)
        # log.info("Ensured 'covid_data_embeddings' table exists in PostgreSQL.")

        # --- Alter existing tables if needed ---
        try:
            pg_hook.run("ALTER TABLE th_covid_data ADD COLUMN IF NOT EXISTS resource_id VARCHAR(255);")
            log.info("Ensured 'resource_id' column exists in 'th_covid_data' table.")
        except Exception as e:
            log.warning(f"Could not alter th_covid_data to add resource_id: {e}")


    @task(
        retries=3,
        retry_delay=timedelta(seconds=10),
    )
    def load_minio_to_postgres(file_info: dict):
        """
        Loads a single transformed CSV file from MinIO into the 'th_covid_data' table in PostgreSQL.
        """
        s3_key = file_info["s3_key"]
        resource_id_from_file_info = file_info["resource_id"]
        
        s3_hook = S3Hook(aws_conn_id="minio_conn")
        pg_hook = PostgresHook(postgres_conn_id="postgres_localhost")
        log = logging.getLogger(__name__)

        log.info(f"Loading s3://{MINIO_BUCKET_NAME}/{s3_key} into PostgreSQL...")

        status = "failed_pg"
        conn = None

        try:
            current_file_status_df = pg_hook.get_pandas_df(
                f"SELECT status FROM file_logs WHERE resource_id = '{resource_id_from_file_info}'"
            )
            current_file_status = current_file_status_df['status'].iloc[0] if not current_file_status_df.empty else None

            if current_file_status == "postgres_loaded":
                log.info(f"Resource {resource_id_from_file_info} already loaded to PostgreSQL. Skipping.")
                status = "postgres_loaded"
                return {"resource_id": resource_id_from_file_info, "s3_key": s3_key, "status": status}

            file_content = s3_hook.read_key(key=s3_key, bucket_name=MINIO_BUCKET_NAME)
            df = pd.read_csv(io.StringIO(file_content), sep="|", low_memory=False)

            df.columns = [col.replace(' ', '_').lower() for col in df.columns]

            if 'no.' in df.columns:
                df = df.rename(columns={'no.': '_id'})
            elif 'no' in df.columns and '_id' not in df.columns:
                df = df.rename(columns={'no': '_id'})

            if '_id' not in df.columns:
                 log.warning(f"No explicit ID column found in {s3_key}. Generating hash-based IDs.")
                 df['_id'] = [int(hashlib.sha256(str(row.values).encode('utf-8')).hexdigest(), 16) % (2**31 - 1) for idx, row in df.iterrows()]
            
            df['_id'] = pd.to_numeric(df['_id'], errors='coerce')
            initial_rows = len(df)
            df.dropna(subset=['_id'], inplace=True)

            if len(df) < initial_rows:
                log.warning(f"Dropped {initial_rows - len(df)} rows from {s3_key} due to missing or invalid _id.")
                if len(df) == 0:
                    status = "skipped_no_valid_ids"
                    return {"resource_id": resource_id_from_file_info, "s3_key": s3_key, "status": status}

            df['_id'] = df['_id'].astype(int)
            df = df.loc[:, ~df.columns.str.contains('^unnamed')]

            if 'age' in df.columns:
                df['age'] = pd.to_numeric(df['age'], errors='coerce').fillna(0).astype(int)

            df['resource_id'] = resource_id_from_file_info

            expected_columns_df = [
                '_id', 'resource_id', 'announce_date', 'notified_date', 'sex', 'age', 'unit',
                'nationality', 'province_of_isolation', 'risk', 'province_of_onset', 'district_of_onset'
            ]
            
            for col in expected_columns_df:
                if col not in df.columns:
                    df[col] = None

            df = df[expected_columns_df]
            records = df.to_dict(orient='records')
            
            conn = pg_hook.get_conn()
            cur = conn.cursor()

            pg_col_names_for_insert = [
                '_id', 'resource_id', 'announce_date', 'notified_date', 'sex', 'age', 'unit',
                'nationality', 'province_of_isolation', 'risk', 'province_of_onset', 'district_of_onset'
            ]
            
            columns_sql = ", ".join(pg_col_names_for_insert)
            placeholders_sql = ", ".join(["%s"] * len(pg_col_names_for_insert))
            update_set_sql = ", ".join([f'{col} = EXCLUDED.{col}' for col in pg_col_names_for_insert if col != '_id'])
            update_set_sql += ", loaded_at = NOW(), processed_for_embedding = FALSE"

            insert_sql_template = f"""
                INSERT INTO th_covid_data ({columns_sql}, loaded_at)
                VALUES ({placeholders_sql}, NOW())
                ON CONFLICT (_id) DO UPDATE SET {update_set_sql};
            """

            insert_count = 0
            for record_dict in records:
                values = [record_dict.get(col) for col in pg_col_names_for_insert]
                if values[0] is None:
                    continue
                cur.execute(insert_sql_template, values)
                insert_count += 1
            
            conn.commit()
            log.info(f"Successfully loaded {insert_count} records from {s3_key} into th_covid_data.")
            status = "postgres_loaded"

        except Exception as e:
            log.error(f"Error loading {s3_key} to PostgreSQL: {e}")
            if conn:
                conn.rollback()
            raise

        finally:
            conn_log = None
            try:
                conn_log = pg_hook.get_conn()
                cur_log = conn_log.cursor()
                cur_log.execute(
                    """
                    INSERT INTO file_logs (resource_id, s3_key, status, processed_at, last_attempt_at)
                    VALUES (%s, %s, %s, NOW(), NOW())
                    ON CONFLICT (resource_id) DO UPDATE
                    SET s3_key = EXCLUDED.s3_key,
                        status = EXCLUDED.status,
                        processed_at = COALESCE(file_logs.processed_at, NOW()),
                        last_attempt_at = NOW();
                    """,
                    (resource_id_from_file_info, s3_key, status)
                )
                conn_log.commit()
            except Exception as log_err:
                log.error(f"Error logging status to file_logs from load_minio_to_postgres for {resource_id_from_file_info}: {log_err}")
                if conn_log:
                    conn_log.rollback()
            finally:
                if conn_log:
                    cur_log.close()
                    conn_log.close()
            
            if conn:
                cur.close()
                conn.close()

        return {"resource_id": resource_id_from_file_info, "s3_key": s3_key, "status": status}

    # --- Global Model Instance (Singleton Pattern) for SentenceTransformers ---
    _sentence_transformer_model_instance = None

    def get_sentence_transformer_model():
        global _sentence_transformer_model_instance
        if _sentence_transformer_model_instance is None:
            log.info(f"Loading SentenceTransformer model '{EMBEDDING_MODEL_NAME}'...")
            _sentence_transformer_model_instance = SentenceTransformer(EMBEDDING_MODEL_NAME)
            log.info("SentenceTransformer model loaded.")
        return _sentence_transformer_model_instance

    # --- RAG TASKS ---

    @task(trigger_rule=TriggerRule.ALL_DONE)
    def initialize_chroma_client_and_collection():
        """
        Initializes ChromaDB client and ensures the collection exists.
        """
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        log.info(f"Connected to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}")

        class STSEmbeddingFunction(embedding_functions.EmbeddingFunction):
            def __call__(self, input: embedding_functions.Documents) -> embedding_functions.Embeddings:
                model = get_sentence_transformer_model()
                instructed_input = [f"query: {doc}" for doc in input]
                embeddings = model.encode(instructed_input).tolist()
                return embeddings

        st_ef = STSEmbeddingFunction()

        try:
            collection = client.get_or_create_collection(
                name=CHROMA_COLLECTION_NAME,
                embedding_function=st_ef
            )
            log.info(f"ChromaDB collection '{CHROMA_COLLECTION_NAME}' ensured/created.")
        except Exception as e:
            log.error(f"Error ensuring ChromaDB collection: {e}")
            raise

        return {"collection_name": CHROMA_COLLECTION_NAME}

    @task(
        retries=3,
        retry_delay=timedelta(seconds=10),
    )
    def index_data_in_chroma(chroma_config: dict):
        """
        Implements a FULL HYBRID indexing strategy (Parent/Child Documents).
        - One "parent" document with rich context.
        - Child documents for "province", "nationality", and "district" for precise matching.
        """
        pg_hook = PostgresHook(postgres_conn_id="postgres_localhost")
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        collection = client.get_collection(name=chroma_config["collection_name"])
        log.info(f"Retrieved ChromaDB collection '{chroma_config['collection_name']}'.")

        BATCH_SIZE_PG = 3000  # Reduced to manage memory for creating multiple embeddings per record
        BATCH_SIZE_CHROMA = 1024 # Chroma can handle larger write batches
        total_indexed_records = 0

        try:
            unprocessed_resource_ids_df = pg_hook.get_pandas_df("""
                SELECT DISTINCT resource_id FROM th_covid_data
                WHERE processed_for_embedding = FALSE AND resource_id IS NOT NULL;
            """)
            
            if unprocessed_resource_ids_df.empty:
                log.info("No source files with unprocessed records. Skipping Chroma indexing.")
                raise AirflowSkipException("No records to embed.")

            unprocessed_resource_ids = [r for r in unprocessed_resource_ids_df['resource_id'].tolist() if r is not None]
            log.info(f"Found {len(unprocessed_resource_ids)} source files with unprocessed records.")

            for current_resource_id in unprocessed_resource_ids:
                log.info(f"Processing records for resource_id: {current_resource_id}")
                file_offset = 0
                
                while True:
                    current_records_df = pg_hook.get_pandas_df(f"""
                        SELECT _id, resource_id, sex, age, nationality, risk, province_of_onset, district_of_onset
                        FROM th_covid_data
                        WHERE processed_for_embedding = FALSE AND resource_id = '{current_resource_id}'
                        ORDER BY _id
                        LIMIT {BATCH_SIZE_PG} OFFSET {file_offset}
                    """)

                    if current_records_df.empty:
                        break

                    records_to_process = current_records_df.to_dict(orient='records')
                    log.info(f"Fetched {len(records_to_process)} records for full hybrid embedding.")

                    documents = []
                    metadatas = []
                    ids = []
                    processed_ids_in_batch = []

                    for record in records_to_process:
                        record_id = record['_id']
                        province = record.get('province_of_onset')
                        nationality = record.get('nationality')
                        district = record.get('district_of_onset')

                        # ★★★ 1. CREATE THE PARENT (RICH CONTEXT) DOCUMENT ★★★
                        parent_text = (
                            f"A case involving a {record.get('age', 'N/A')} year old {record.get('sex', 'N/A')} of {nationality or 'N/A'} nationality "
                            f"was identified in the province of {province or 'N/A'}, district of {district or 'N/A'}. "
                            f"The risk was: {record.get('risk', 'N/A')}."
                        )
                        documents.append(parent_text)
                        # The metadata for ALL related documents must contain the full text for the LLM
                        base_metadata = {
                            "record_id": record_id,
                            "full_text": parent_text 
                        }
                        
                        parent_metadata = base_metadata.copy()
                        parent_metadata["type"] = "context"
                        metadatas.append(parent_metadata)
                        ids.append(f"{record_id}_context")

                        # ★★★ 2. CREATE THE PROVINCE CHILD DOCUMENT ★★★
                        if province:
                            # documents.append(province)
                            documents.append(f"Province of onset: {province}")
                            province_metadata = base_metadata.copy()
                            province_metadata["type"] = "province"
                            metadatas.append(province_metadata)
                            ids.append(f"{record_id}_province")

                        # ★★★ 3. CREATE THE NATIONALITY CHILD DOCUMENT ★★★
                        if nationality:
                            # documents.append(nationality)
                            documents.append(f"Nationality: {nationality}")
                            nationality_metadata = base_metadata.copy()
                            nationality_metadata["type"] = "nationality"
                            metadatas.append(nationality_metadata)
                            ids.append(f"{record_id}_nationality")
                            
                        # ★★★ 4. CREATE THE DISTRICT CHILD DOCUMENT ★★★
                        if district:
                            # documents.append(district)
                            documents.append(f"District of onset: {district}")
                            district_metadata = base_metadata.copy()
                            district_metadata["type"] = "district"
                            metadatas.append(district_metadata)
                            ids.append(f"{record_id}_district")
                        
                        processed_ids_in_batch.append(record_id)

                    if documents:
                        for i in range(0, len(documents), BATCH_SIZE_CHROMA):
                            sub_batch_docs = documents[i:i + BATCH_SIZE_CHROMA]
                            sub_batch_metas = metadatas[i:i + BATCH_SIZE_CHROMA]
                            sub_batch_ids = ids[i:i + BATCH_SIZE_CHROMA]
                            
                            log.info(f"Upserting sub-batch of {len(sub_batch_docs)} parent/child documents to ChromaDB...")
                            collection.upsert(
                                documents=sub_batch_docs,
                                metadatas=sub_batch_metas,
                                ids=sub_batch_ids
                            )
                            log.info(f"Successfully upserted sub-batch.")
                        
                        total_indexed_records += len(processed_ids_in_batch)

                    if processed_ids_in_batch:
                        pg_hook.run(f"""
                            UPDATE th_covid_data
                            SET processed_for_embedding = TRUE
                            WHERE _id IN ({', '.join(map(str, processed_ids_in_batch))});
                        """)
                        log.info(f"Marked {len(processed_ids_in_batch)} source records as processed in PostgreSQL.")
                    
                    file_offset += BATCH_SIZE_PG

                # Verification logic for the entire file
                unprocessed_count_for_file_df = pg_hook.get_pandas_df(f"SELECT COUNT(*) AS count FROM th_covid_data WHERE resource_id = '{current_resource_id}' AND processed_for_embedding = FALSE;")
                if unprocessed_count_for_file_df['count'].iloc[0] == 0:
                    log.info(f"All records for resource_id '{current_resource_id}' are embedded. Updating file_logs.")
                    pg_hook.run("UPDATE file_logs SET status = %s, last_attempt_at = NOW() WHERE resource_id = %s;", parameters=("chroma_indexed", current_resource_id))
            
            overall_status = "chroma_indexed"

        except AirflowSkipException:
            overall_status = "skipped"
            raise
        except Exception as e:
            log.error(f"Error during ChromaDB indexing or PostgreSQL update: {e}")
            overall_status = "failed_chroma"
            raise
        finally:
            log.info(f"Chroma indexing task completed with status: {overall_status}. Total source records processed this run: {total_indexed_records}")

        return {"indexed_record_count": total_indexed_records, "overall_status": overall_status}


    # --- DAG Flow Definition ---
    
    # 1. Setup and Fetch
    create_table_task = create_covid_data_table()
    ckan_metadata_task = fetch_ckan_metadata()
    new_csv_urls_task = get_csv_urls_from_ckan(ckan_metadata_task)

    # 2. ETL Branch (CKAN -> S3 -> Postgres)
    processed_files_info_task = extract_transform_and_store_single_file.partial().expand(url=new_csv_urls_task)
    successfully_s3_loaded_files_task = filter_successful_s3_uploads(processed_files_info_task)
    load_to_postgres_task_instance = load_minio_to_postgres.partial().expand(file_info=successfully_s3_loaded_files_task)

    # 3. RAG Indexing Branch (Postgres -> ChromaDB)
    chroma_init_task_instance = initialize_chroma_client_and_collection()
    chroma_indexing_task_instance = index_data_in_chroma(chroma_config=chroma_init_task_instance)

    # --- Set Dependencies ---
    create_table_task >> ckan_metadata_task >> new_csv_urls_task
    new_csv_urls_task >> processed_files_info_task >> successfully_s3_loaded_files_task >> load_to_postgres_task_instance
    
    # The RAG branch depends on the ETL branch being complete (or skipped)
    load_to_postgres_task_instance >> chroma_init_task_instance >> chroma_indexing_task_instance