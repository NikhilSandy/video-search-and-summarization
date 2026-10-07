# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Opt-in, single-inference camera-health classification and single-incident delivery."""

import json

CAMERA_HEALTH_CATEGORY = "Camera Health"
CAMERA_HEALTH_CLASSES = (
    "view_obstruction",
    "bright_light_interference",
    "camera_view_changed",
    "illumination_too_low",
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_camera_health_class(text: str) -> str:
    """Accept exactly one allowed class; malformed output is never healthy.

    Legacy arrays are rejected rather than selecting their first item: only
    the model can choose the primary visible cause across the video frames.
    """
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4].strip()
    data = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(data, dict) or set(data) != {"class"}:
        raise ValueError('Expected exactly {"class": "one_allowed_label"}')
    label = data["class"]
    if not isinstance(label, str) or label not in (*CAMERA_HEALTH_CLASSES, "healthy"):
        raise ValueError("class must be one allowed string label")
    return label


def camera_health_incidents(vision_llm, params):
    """Translate this application's captions to incidents without another VLM call.

    Matching both prompts opts in only this application's inference requests.
    Malformed matching responses raise ValueError instead of looking healthy.
    Imports stay local so the strict JSON parser also runs without protobuf.
    """
    from server.protos import ext_pb2

    queries = [
        q
        for q in vision_llm.llm.queries
        if q.prompts.get("system") == params["system_prompt"]
        and q.prompts.get("user") == params["prompt"]
    ]
    if not queries:
        return None
    if len(queries) != 1:
        raise ValueError("Expected one camera-health query per caption")
    query = queries[0]
    label = parse_camera_health_class(query.response)
    sensor_id = vision_llm.sensor.id
    if (
        not sensor_id
        or not vision_llm.HasField("timestamp")
        or not vision_llm.HasField("end")
    ):
        raise ValueError("Camera-health caption is missing sensor or window timestamps")
    if label == "healthy":
        return []
    incident = ext_pb2.Incident()
    incident.sensorId = sensor_id
    incident.timestamp.CopyFrom(vision_llm.timestamp)
    incident.end.CopyFrom(vision_llm.end)
    incident.category = label
    incident.isAnomaly = True
    incident.llm.CopyFrom(vision_llm.llm)
    if vision_llm.frames:
        incident.frameIds.extend(
            dict.fromkeys([vision_llm.frames[0].id, vision_llm.frames[-1].id])
        )
    stream_id = vision_llm.info.get("streamId", "")
    if stream_id:
        incident.objectIds.append(stream_id)
    incident.place.id = sensor_id
    incident.place.name = sensor_id
    incident.analyticsModule.id = "camera-health"
    incident.analyticsModule.description = (
        "Single-call VLM camera-health classification"
    )
    incident.analyticsModule.source = "rtvi-vlm"
    incident.analyticsModule.version = vision_llm.version
    incident.info.update(dict(vision_llm.info))
    incident.info.pop("detectedClasses", None)
    incident.info.update(
        {
            "verdict": "confirmed",
            "incidentDetected": "true",
            "alertCategory": label,
            "alertClass": label,
            "alertRuleCategory": CAMERA_HEALTH_CATEGORY,
            "selectedClass": label,
            "classificationPolicy": "single_most_likely",
            "classificationResponse": query.response,
            "classificationStatus": "valid",
            "prompt": params["prompt"],
            "systemPrompt": params["system_prompt"],
        }
    )
    return [incident]
