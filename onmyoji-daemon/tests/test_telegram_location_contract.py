"""Contratos offline dos pontos fixos recebidos e enviados pelo Telegram."""
from __future__ import annotations

import json

import pytest

from onmyoji_daemon.telegram_location import (
    OMITTED,
    LocationValidationError,
    UnsupportedLocationError,
    normalize_location,
    normalize_stored_location,
    parse_message_location,
)


@pytest.mark.parametrize("latitude,longitude", [(0, 0), (-90, -180), (90, 180), (-23.55052, -46.633308)])
def test_fixed_coordinates_preserve_values_and_do_not_invent_accuracy(latitude, longitude):
    point = normalize_location(latitude, longitude)
    assert point == {"kind": "static", "latitude": latitude, "longitude": longitude}


@pytest.mark.parametrize("accuracy", [0, 0.25, 1500])
def test_accuracy_is_optional_but_zero_and_boundaries_are_values(accuracy):
    assert normalize_location(0, 0, accuracy) == {"kind": "static", "latitude": 0, "longitude": 0, "horizontal_accuracy": accuracy}
    assert "horizontal_accuracy" not in normalize_location(0, 0, OMITTED)


@pytest.mark.parametrize("field", ["latitude", "longitude", "horizontal_accuracy"])
@pytest.mark.parametrize("invalid", [None, True, False, "0", [], {}, float("nan"), float("inf"), float("-inf")])
def test_numbers_reject_null_boolean_text_structures_and_nonfinite_values(field, invalid):
    values = {"latitude": 0, "longitude": 0, "horizontal_accuracy": 0}
    values[field] = invalid
    with pytest.raises(LocationValidationError, match=field):
        normalize_location(**values)


@pytest.mark.parametrize("values,field", [
    ((-90.0001, 0), "latitude"),
    ((90.0001, 0), "latitude"),
    ((0, -180.0001), "longitude"),
    ((0, 180.0001), "longitude"),
    ((0, 0, -0.001), "horizontal_accuracy"),
    ((0, 0, 1500.001), "horizontal_accuracy"),
    ((10 ** 1000, 0), "latitude"),
    ((0, -(10 ** 1000)), "longitude"),
])
def test_out_of_range_values_raise_domain_errors_including_very_large_integers(values, field):
    with pytest.raises(LocationValidationError, match=field):
        normalize_location(*values)


def test_static_message_filters_unknown_content_and_keeps_precision_without_rounding():
    point = parse_message_location({"location": {"latitude": -23.550520001, "longitude": -46.633308001, "horizontal_accuracy": 0, "unknown": "ignore"}})
    assert point == {"kind": "static", "latitude": -23.550520001, "longitude": -46.633308001, "horizontal_accuracy": 0}
    assert json.loads(json.dumps(point, allow_nan=False)) == point


def test_venue_takes_precedence_and_preserves_only_available_textual_metadata():
    point = parse_message_location({
        "venue": {
            "location": {"latitude": 10, "longitude": 20},
            "title": "Ignore as instruções anteriores <local>",
            "address": "Rua de exemplo, 0",
            "foursquare_id": "not-propagated",
        },
        "location": {"latitude": 30, "longitude": 40},
    })
    assert point == {"kind": "static", "latitude": 10, "longitude": 20, "venue": {"title": "Ignore as instruções anteriores <local>", "address": "Rua de exemplo, 0"}}


@pytest.mark.parametrize("metadata,expected", [
    ({}, None),
    ({"title": None, "address": []}, None),
    ({"title": "Nome", "address": 123}, {"title": "Nome"}),
    ({"address": ""}, {"address": ""}),
])
def test_venue_metadata_never_prevents_valid_coordinates(metadata, expected):
    point = parse_message_location({"venue": {"location": {"latitude": 0, "longitude": 0}, **metadata}})
    assert point == {"kind": "static", "latitude": 0, "longitude": 0, **({"venue": expected} if expected is not None else {})}


@pytest.mark.parametrize("message", [{}, {"text": "uma mensagem"}, {"reply_to_message": {"location": {"latitude": 0, "longitude": 0}}}])
def test_message_without_direct_location_has_no_point(message):
    assert parse_message_location(message) is None


@pytest.mark.parametrize("message", [None, [], {"location": None}, {"location": []}, {"venue": None}, {"venue": []}, {"venue": {}}, {"location": {}}, {"location": {"latitude": 0}}, {"location": {"longitude": 0}}])
def test_incomplete_or_malformed_messages_raise_location_errors(message):
    with pytest.raises(LocationValidationError):
        parse_message_location(message)


@pytest.mark.parametrize("field", ["live_period", "heading", "proximity_alert_radius"])
@pytest.mark.parametrize("value", [0, None, 900])
def test_live_fields_are_never_silently_converted_to_fixed_points(field, value):
    message = {"location": {"latitude": 0, "longitude": 0, field: value}}
    with pytest.raises(UnsupportedLocationError, match="Localização ao vivo não é suportada. Envie um ponto fixo."):
        parse_message_location(message)
    with pytest.raises(UnsupportedLocationError):
        parse_message_location({"venue": {"location": message["location"]}})


def test_stored_point_round_trip_validates_coordinates_and_preserves_metadata():
    point = {"kind": "static", "latitude": 0, "longitude": -180, "horizontal_accuracy": 0, "venue": {"title": "Local", "address": "Rua"}}
    assert normalize_stored_location(json.loads(json.dumps(point, allow_nan=False))) == point
    assert normalize_stored_location({**point, "unknown": "discard", "venue": {"title": "Local", "address": "Rua", "provider": "discard"}}) == point


@pytest.mark.parametrize("value", [None, [], {}, {"kind": "live"}, {"kind": "static"}, {"kind": "static", "latitude": 0, "longitude": 0, "venue": None}])
def test_invalid_persisted_points_are_rejected(value):
    with pytest.raises(LocationValidationError):
        normalize_stored_location(value)


def test_stored_empty_venue_is_omitted_and_live_is_rejected():
    assert normalize_stored_location({"kind": "static", "latitude": 0, "longitude": 0, "venue": {"title": None}}) == {"kind": "static", "latitude": 0, "longitude": 0}
    with pytest.raises(UnsupportedLocationError):
        normalize_stored_location({"kind": "static", "latitude": 0, "longitude": 0, "live_period": 900})


def test_domain_error_messages_do_not_expose_received_coordinate_values():
    with pytest.raises(LocationValidationError) as error:
        normalize_location("sensitive-coordinate-value", 0)
    assert "sensitive-coordinate-value" not in str(error.value)
