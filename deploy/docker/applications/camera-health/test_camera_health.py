# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Camera-health output contract and protobuf delivery regression checks."""

import json
import unittest
from pathlib import Path

from server.camera_health import (
    CAMERA_HEALTH_CLASSES,
    camera_health_incidents,
    parse_camera_health_class,
)
from server.protos import ext_pb2, nv_pb2

PARAMS = {
    "system_prompt": "camera health test system",
    "prompt": "camera health test prompt",
}


def caption(response):
    message = nv_pb2.VisionLLM(version="test-model")
    message.sensor.id = "test-camera"
    message.timestamp.FromJsonString("2026-10-05T10:00:00Z")
    message.end.FromJsonString("2026-10-05T10:00:04Z")
    message.info.update(
        {"requestId": "test-request", "chunkIdx": "3", "streamId": "stream-uuid"}
    )
    message.frames.add(id="first-frame")
    message.frames.add(id="last-frame")
    query = message.llm.queries.add(id="test-request:3", response=response)
    query.prompts.update({"system": PARAMS["system_prompt"], "user": PARAMS["prompt"]})
    return message


class CameraHealthTests(unittest.TestCase):
    def test_healthy_emits_no_incidents(self):
        self.assertEqual(parse_camera_health_class('{"class":"healthy"}'), "healthy")
        self.assertEqual(
            camera_health_incidents(caption('{"class":"healthy"}'), PARAMS), []
        )

    def test_every_class_serializes_to_its_own_incident(self):
        for label in CAMERA_HEALTH_CLASSES:
            with self.subTest(label=label):
                source = caption(json.dumps({"class": label}))
                messages = camera_health_incidents(source, PARAMS)
                self.assertEqual(len(messages), 1)
                decoded = ext_pb2.Incident.FromString(messages[0].SerializeToString())
                self.assertEqual(decoded.category, label)
                self.assertEqual(decoded.sensorId, "test-camera")
                self.assertEqual(decoded.timestamp, source.timestamp)
                self.assertEqual(decoded.end, source.end)
                self.assertTrue(decoded.isAnomaly)
                self.assertEqual(decoded.info["verdict"], "confirmed")
                self.assertEqual(decoded.info["incidentDetected"], "true")
                self.assertEqual(decoded.info["requestId"], "test-request")
                self.assertEqual(decoded.info["chunkIdx"], "3")
                self.assertEqual(decoded.info["selectedClass"], label)
                self.assertEqual(
                    decoded.info["classificationPolicy"], "single_most_likely"
                )
                self.assertNotIn("detectedClasses", decoded.info)
                self.assertEqual(
                    decoded.llm.queries[0].response, source.llm.queries[0].response
                )
                self.assertEqual(list(decoded.frameIds), ["first-frame", "last-frame"])

    def test_multi_class_outputs_are_rejected_without_choosing_first(self):
        for response in (
            '{"classes":[]}',
            '{"classes":["view_obstruction"]}',
            '{"classes":["illumination_too_low","view_obstruction"]}',
            '{"class":["view_obstruction"]}',
            '{"class":["view_obstruction","illumination_too_low"]}',
            '{"class":"view_obstruction,illumination_too_low"}',
            '{"class":"view_obstruction","classes":["illumination_too_low"]}',
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                camera_health_incidents(caption(response), PARAMS)

    def test_incident_metadata_is_independent_of_source(self):
        source = caption('{"class":"view_obstruction"}')
        source.info["detectedClasses"] = '["view_obstruction","illumination_too_low"]'
        messages = camera_health_incidents(source, PARAMS)
        self.assertEqual(len(messages), 1)
        self.assertNotIn("detectedClasses", messages[0].info)
        messages[0].info["verdict"] = "changed"
        self.assertNotIn("verdict", source.info)
        self.assertIn("detectedClasses", source.info)

    def test_request_schema_enforces_same_scalar_labels(self):
        config = json.loads((Path(__file__).parent / "camera-health.json").read_text())
        schema = config["params"]["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["required"], ["class"])
        self.assertEqual(set(schema["properties"]), {"class"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["class"]["type"], "string")
        self.assertEqual(
            set(schema["properties"]["class"]["enum"]),
            {*CAMERA_HEALTH_CLASSES, "healthy"},
        )

    def test_complete_json_fence_is_accepted(self):
        self.assertEqual(
            parse_camera_health_class('```json\n{"class":"healthy"}\n```'), "healthy"
        )

    def test_invalid_outputs_are_errors_instead_of_healthy_results(self):
        for response in (
            "",
            "Yes",
            "No",
            "true",
            "{}",
            "[]",
            "null",
            '{"class":null}',
            '{"class":true}',
            '{"class":1}',
            '{"class":{}}',
            '{"class":""}',
            '{"class":"View Obstruction"}',
            '{"class":"unknown"}',
            '{"class":"healthy","reason":"true"}',
            '{"class":"healthy","class":"view_obstruction"}',
            'Here is the result: {"class":"healthy"}',
            '{"class":"healthy"',
            '{"class":"healthy"}\n{"class":"view_obstruction"}',
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                camera_health_incidents(caption(response), PARAMS)

    def test_hailing_and_collapse_captions_are_ignored(self):
        for response in ("Yes", "No", '{"class":"view_obstruction"}'):
            source = caption(response)
            source.llm.queries[0].prompts["user"] = "existing person collapse prompt"
            self.assertIsNone(camera_health_incidents(source, PARAMS))
            source.llm.queries[0].prompts["user"] = PARAMS["prompt"]
            source.llm.queries[0].prompts["system"] = "existing hailing system prompt"
            self.assertIsNone(camera_health_incidents(source, PARAMS))
        source = caption('{"classes":["view_obstruction"]}')
        source.llm.queries[0].prompts["user"] = "previous camera health list prompt"
        self.assertIsNone(camera_health_incidents(source, PARAMS))

    def test_multiple_matching_queries_cannot_emit_multiple_incidents(self):
        source = caption('{"class":"view_obstruction"}')
        source.llm.queries.add().CopyFrom(source.llm.queries[0])
        with self.assertRaises(ValueError):
            camera_health_incidents(source, PARAMS)

    def test_missing_sensor_or_timestamps_are_errors(self):
        for field in ("sensor", "timestamp", "end"):
            source = caption('{"class":"view_obstruction"}')
            source.ClearField(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                camera_health_incidents(source, PARAMS)


if __name__ == "__main__":
    unittest.main()
