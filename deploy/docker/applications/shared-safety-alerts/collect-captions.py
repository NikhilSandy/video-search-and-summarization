# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
import json, os, signal, sys
from datetime import datetime, timezone
from pathlib import Path
from kafka import KafkaConsumer
from google.protobuf.json_format import MessageToDict
from server.protos import nv_pb2
prompt=json.loads(Path(sys.argv[1]).read_text())['params']['prompt']
consumer=KafkaConsumer('mdx-vlm-captions',bootstrap_servers=os.environ['KAFKA_BOOTSTRAP_SERVERS'],group_id='vss-single-playback-audit-'+str(os.getpid()),enable_auto_commit=False,auto_offset_reset='latest',max_poll_records=100)
stop=False
ready=False
def shutdown(*_):
 global stop
 stop=True
signal.signal(signal.SIGTERM,shutdown)
signal.signal(signal.SIGINT,shutdown)
try:
 while not stop:
  batches=consumer.poll(timeout_ms=1000)
  if not ready and consumer.assignment():
   print(json.dumps({'status':'consumer_ready','pid':os.getpid()}),flush=True)
   ready=True
  for records in batches.values():
   for record in records:
    if dict(record.headers or []).get('message_type')!=b'vision_llm': continue
    caption=nv_pb2.VisionLLM(); caption.ParseFromString(record.value)
    if not any(q.prompts.get('user')==prompt for q in caption.llm.queries): continue
    data=MessageToDict(caption,preserving_proto_field_name=True)
    response=caption.llm.queries[0].response if caption.llm.queries else ''
    try:
     parsed=json.loads(response) if response else None
    except json.JSONDecodeError:
     parsed=None
    print(json.dumps({'received_at':datetime.now(timezone.utc).isoformat(),'response':response,'parsed':parsed,'caption':data}),flush=True)
finally:
 consumer.close()
