import subprocess
import json
import os
import sys
from datetime import datetime
from influxdb_client import InfluxDBClient
from influxdb_client.rest import ApiException


def create_bucket_if_not_exists(client, bucket_name, org):
    try:
        buckets_api = client.buckets_api()
        org_id = client.organizations_api().find_organizations(org=org)[0].id
        buckets = buckets_api.find_buckets().buckets
        if not any(bucket.name == bucket_name for bucket in buckets):
            buckets_api.create_bucket(bucket_name=bucket_name, org_id=org_id)
            print(f"Bucket {bucket_name} created successfully.")
        else:
            print(f"Bucket {bucket_name} already exists.")
    except ApiException as e:
        print(f"Error creating bucket: {e}")


def write_to_influxdb(db_name, org, token, timestamp, read_speed_mb, completion_latency):
    client = InfluxDBClient(url="http://influxdb:8086", token=token, org=org)
    #client = InfluxDBClient(url="http://influxdb:8086", token=token, org=org)
    create_bucket_if_not_exists(client, db_name, org)
    write_api = client.write_api()

    json_body = [
        {
            "measurement": "FIO",
            "tags": {
                "runId": "fio_run",
                "hostname": "localhost"
            },
            "time": timestamp,
            "fields": {
                "Read_bandwidth_(MB/s)": read_speed_mb,
                "Completion_Latency_ms": completion_latency if completion_latency is not None else "N/A"
            }
        }
    ]

    write_api.write(bucket=db_name, record=json_body)
    write_api.__del__()  # Explicitly call the destructor to flush all pending writes
    client.close()


def run_fio(job_file, db_name, org, token):
    try:
        if os.geteuid() != 0:
            print("This script must be run as root.")
            sys.exit(1)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        process = subprocess.Popen(
            ['fio', '--output-format=json', '--status-interval=1', job_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )

        buffer = ""

        while True:
            output_line = process.stdout.readline()
            if output_line == '' and process.poll() is not None:
                break
            if output_line:
                buffer += output_line.strip()
                if buffer.startswith('{') and buffer.endswith('}'):
                    try:
                        fio_output = json.loads(buffer)
                        buffer = ""

                        if 'jobs' in fio_output and len(fio_output['jobs']) > 0:
                            job = fio_output['jobs'][0]

                            read_speed = job['read'].get('bw', 0)
                            clat_ns = job['read'].get('clat_ns', {})
                            completion_latency = clat_ns.get('mean',
                                                             0) / 1000000 if 'mean' in clat_ns else None  # Convert to ms

                            read_speed_mb = read_speed / 1024
                            timestamp = datetime.utcnow().isoformat()

                            if completion_latency is not None:
                                print(
                                    f"Timestamp: {timestamp}, Sequential Read Speed: {read_speed_mb:.2f} MB/s, Completion Latency: {completion_latency:.2f} ms")
                            else:
                                print(f"Timestamp: {timestamp}, Sequential Read Speed: {read_speed_mb:.2f} MB/s")

                            write_to_influxdb(db_name, org, token, timestamp, read_speed_mb, completion_latency)
                    except json.JSONDecodeError:
                        pass

        if process.returncode != 0:
            stderr_output = process.stderr.read()
            print("Error running FIO job:")
            print(stderr_output)
    except Exception as e:
        print("An unexpected error occurred:")
        print(e)


if __name__ == "__main__":
    db_name = os.getenv("DB_NAME")
    token = os.getenv("INFLUXDB_TOKEN")
    org = os.getenv("INFLUXDB_ORG")
    fio_job_file = os.getenv("FIO_JOB_FILE")

    if not db_name:
        print("Error: DB_NAME environment variable not set.")
        sys.exit(1)
    if not token:
        print("Error: INFLUXDB_TOKEN environment variable not set.")
        sys.exit(1)
    if not org:
        print("Error: INFLUXDB_ORG environment variable not set.")
        sys.exit(1)
    if not fio_job_file:
        print("Error: FIO_JOB_FILE environment variable not set.")
        sys.exit(1)

    run_fio(fio_job_file, db_name, org, token)
