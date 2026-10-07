# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Visual-cue text cannot override the model's explicit Yes/No decision."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from server.rtvi_stream_handler import _alert_trigger_tokens


DECISION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "visual_cues_decision", "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "visual_cues": {"type": "array", "items": {"type": "string"}},
                "decision": {"type": "string", "enum": ["Yes", "No"]},
            },
            "required": ["visual_cues", "decision"],
            "additionalProperties": False,
        },
    },
}
FORMAT = SimpleNamespace(json_schema=SimpleNamespace(
    schema_=DECISION_FORMAT["json_schema"]["schema"]
))


@pytest.mark.parametrize("cue", ["Yes is written on the screen.", "A true fall is not visible.",
                                 "The claimed fall is untrue.", "The person stands up."])
def test_cues_cannot_trigger_a_negative_decision(cue):
    text = json.dumps({"visual_cues": [cue], "decision": "No"})
    assert _alert_trigger_tokens(text, FORMAT) == []


def test_only_the_model_decision_controls_the_trigger():
    # Scene-specific correctness belongs to the prompt; the decoder reads the decision.
    text = json.dumps({"visual_cues": ["The person stands up."], "decision": "Yes"})
    assert _alert_trigger_tokens(text, FORMAT) == ["yes"]


@pytest.mark.parametrize("text", ["Yes", "{}", "[]", '{"decision":true}',
                                 '{"decision":"yes"}', '{"decision":"unclear"}'])
def test_missing_or_invalid_explicit_decision_is_rejected(text):
    with pytest.raises(ValueError):
        _alert_trigger_tokens(text, FORMAT)


@pytest.mark.parametrize("text,expected", [("Yes", ["yes"]), ("No", []), ("true", ["true"])])
def test_existing_unstructured_rules_keep_their_format(text, expected):
    assert _alert_trigger_tokens(text) == expected


@pytest.mark.parametrize("decision,detected", [("Yes", True), ("No", False)])
def test_incident_serialization_uses_decision_and_preserves_cues(decision, detected):
    from api_models.captions import VlmQuery
    from common.chunk_info import ChunkInfo
    from models.base_vlm_model import VlmModelOutput
    from server.rtvi_stream_handler import RequestInfo, RTVIStreamHandler
    from vlm_pipeline.vlm_pipeline import PipelineChunkResult

    handler = object.__new__(RTVIStreamHandler)
    handler._loaded_models_info = SimpleNamespace(id="test-model")
    handler._place_config = dict(name="camera", type="office", lat=0, lon=0, alt=0,
                                coordinate_x=0, coordinate_y=0)
    handler._analytics_module_config = dict(id="vlm", description="test", source="rtvi-vlm")
    query = VlmQuery(id=uuid4(), model="test-model", prompt="Observe then decide",
                     alert_category="person_collapsing", response_format=DECISION_FORMAT)
    request = RequestInfo(request_id="cue-regression", assets=[], is_live=True, query=query)
    chunk = ChunkInfo(file="rtsp://example.com/test", chunkIdx=260,
                     start_pts=0, end_pts=4_000_000_000)
    chunk.streamId = "test-stream"
    response = json.dumps({"visual_cues": ["Yes appears on a sign.", "A true fall is unclear."],
                           "decision": decision})
    result = PipelineChunkResult(chunk=chunk, vlm_model_output=VlmModelOutput(output=response),
                                 frame_times=[0, 3.75])
    caption, incident = handler._chunk_result_to_vision_llm(result, request)
    assert caption.info["incidentDetected"] == str(detected).lower()
    assert (incident is not None) == detected
    if detected:
        assert incident.llm.queries[0].response == response


def test_application_prompt_and_schema_are_valid():
    from pathlib import Path
    from api_models.captions import VlmQuery

    configured_path = os.environ.get("COLLAPSE_APP_CONFIG")
    app_path = (
        Path(configured_path)
        if configured_path
        else Path(__file__).resolve().parents[5]
        / "deploy/docker/applications/shared-safety-alerts/person-collapse.json"
    )
    params = json.loads(app_path.read_text())["params"]
    query = VlmQuery(id=uuid4(), model="test-model", **params)
    assert query.response_format.json_schema.schema_["properties"]["decision"]["enum"] == ["Yes", "No"]
    assert query.max_tokens >= 100
