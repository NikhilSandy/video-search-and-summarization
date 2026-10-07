#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Map the selected camera-health caption class to nv.Incident events using the existing Kafka bus."""

import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from kafka import KafkaConsumer, KafkaProducer
from kafka.structs import OffsetAndMetadata, TopicPartition
from server.camera_health import camera_health_incidents
from server.protos import nv_pb2

logger = logging.getLogger("camera-health")


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    params = json.loads((Path(__file__).parent / "camera-health.json").read_text())[
        "params"
    ]
    bootstrap = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    caption_topic = os.environ.get("CAMERA_HEALTH_CAPTIONS_TOPIC", "mdx-vlm-captions")
    incident_topic = os.environ.get(
        "CAMERA_HEALTH_INCIDENTS_TOPIC", "mdx-vlm-incidents"
    )
    if caption_topic == incident_topic:
        raise ValueError("Input and output topics must differ")
    consumer = KafkaConsumer(
        caption_topic,
        bootstrap_servers=bootstrap,
        group_id=os.environ.get("CAMERA_HEALTH_CONSUMER_GROUP", "vss-camera-health-v1"),
        enable_auto_commit=False,
        auto_offset_reset="latest",
        max_poll_records=100,
    )
    producer = KafkaProducer(bootstrap_servers=bootstrap, acks="all", retries=3)
    stop = False

    def shutdown(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    ready = Path("/tmp/camera-health-ready")
    evidence = Path("/evidence/windows.jsonl")
    try:
        with evidence.open("a", buffering=1) as audit:
            while not stop:
                batches = consumer.poll(timeout_ms=1000)
                for partition, records in batches.items():
                    for record in records:
                        headers = dict(record.headers or [])
                        if headers.get("message_type") == b"vision_llm":
                            vision_llm = nv_pb2.VisionLLM()
                            vision_llm.ParseFromString(record.value)
                            try:
                                incidents = camera_health_incidents(vision_llm, params)
                                status = "valid"
                                error = None
                            except ValueError as exc:
                                incidents = []
                                status = "invalid"
                                error = str(exc)
                                logger.error(
                                    "Invalid classification request=%s chunk=%s: %s",
                                    vision_llm.info.get("requestId"),
                                    vision_llm.info.get("chunkIdx"),
                                    error,
                                )
                            if incidents is not None:
                                for incident in incidents:
                                    key = (
                                        f"{incident.info.get('requestId')}:",
                                        f"{incident.info.get('chunkIdx')}:{incident.category}",
                                    )
                                    producer.send(
                                        incident_topic,
                                        key="".join(key).encode(),
                                        value=incident.SerializeToString(),
                                        headers=[("message_type", b"incident")],
                                    ).get(timeout=30)
                                row = {
                                    "processed_at": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                    "window_start": vision_llm.timestamp.ToJsonString(),
                                    "window_end": vision_llm.end.ToJsonString(),
                                    "sensor_id": vision_llm.sensor.id,
                                    "request_id": vision_llm.info.get("requestId"),
                                    "chunk_idx": vision_llm.info.get("chunkIdx"),
                                    "input_tokens": vision_llm.info.get("inputTokens"),
                                    "output_tokens": vision_llm.info.get(
                                        "outputTokens"
                                    ),
                                    "class": (
                                        incidents[0].category
                                        if incidents
                                        else "healthy"
                                        if status == "valid"
                                        else None
                                    ),
                                    "status": status,
                                    "error": error,
                                    "response": vision_llm.llm.queries[0].response,
                                    "topic": record.topic,
                                    "partition": record.partition,
                                    "offset": record.offset,
                                }
                                audit.write(json.dumps(row) + "\n")
                                logger.info(
                                    "sensor=%s chunk=%s class=%s status=%s",
                                    row["sensor_id"],
                                    row["chunk_idx"],
                                    row["class"],
                                    status,
                                )
                        # Commit exactly this processed record after output acknowledgements.
                        consumer.commit(
                            {
                                TopicPartition(
                                    partition.topic, partition.partition
                                ): OffsetAndMetadata(record.offset + 1, "", -1)
                            }
                        )
                if consumer.assignment():
                    ready.touch()
    finally:
        ready.unlink(missing_ok=True)
        producer.close(timeout=10)
        consumer.close()


if __name__ == "__main__":
    main()
