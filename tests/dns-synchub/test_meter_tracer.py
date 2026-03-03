# pyright: reportPrivateUsage=false

import types
from unittest.mock import MagicMock, patch

import pytest

import dns_synchub.meter as metermod
import dns_synchub.tracer as tracermod
from dns_synchub.utils._once import Once


@pytest.fixture(autouse=True)
def reset_singletons() -> None:
    metermod._TelemetryMeter.instance = None
    metermod._TelemetryMeter.set_once = Once()
    tracermod._TelemetryTracer.instance = None
    tracermod._TelemetryTracer.set_once = Once()


def test_telemetry_meter_singleton_and_override_guard() -> None:
    with patch('dns_synchub.meter.metrics.set_meter_provider'):
        first = metermod.telemetry_meter(exporters={'none'})
        second = metermod.telemetry_meter(exporters={'none'})
    assert first is second
    with pytest.raises(RuntimeError, match='Overriding'):
        metermod.telemetry_meter(service_name='other', exporters={'none'})


def test_telemetry_meter_otlp_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('OTEL_EXPORTER_OTLP_ENDPOINT', raising=False)
    with pytest.raises(ValueError, match='OTEL_EXPORTER_OTLP_ENDPOINT'):
        metermod._TelemetryMeter(exporters={'otlp'})


def test_telemetry_meter_otlp_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel-collector:4317')
    with patch('dns_synchub.meter.importlib.import_module', side_effect=ImportError('missing')):
        with pytest.raises(ImportError, match='opentelemetry-exporter-otlp-proto-grpc'):
            metermod._TelemetryMeter(exporters={'otlp'})


def test_telemetry_meter_otlp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel-collector:4317')
    fake_module = types.SimpleNamespace(OTLPMetricExporter=MagicMock())
    with patch('dns_synchub.meter.importlib.import_module', return_value=fake_module):
        meter = metermod._TelemetryMeter(exporters={'otlp'})
    assert meter.meter_provider is not None


def test_telemetry_tracer_singleton_and_override_guard() -> None:
    with patch('dns_synchub.tracer.trace.set_tracer_provider'):
        first = tracermod.telemetry_tracer(exporters={'none'})
        second = tracermod.telemetry_tracer(exporters={'none'})
    assert first is second
    with pytest.raises(RuntimeError, match='Overriding'):
        tracermod.telemetry_tracer(service_name='other', exporters={'none'})


def test_telemetry_tracer_otlp_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('OTEL_EXPORTER_OTLP_ENDPOINT', raising=False)
    with pytest.raises(ValueError, match='OTEL_EXPORTER_OTLP_ENDPOINT'):
        tracermod._TelemetryTracer(exporters={'otlp'})


def test_telemetry_tracer_otlp_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel-collector:4317')
    with patch('dns_synchub.tracer.importlib.import_module', side_effect=ImportError('missing')):
        with pytest.raises(ImportError, match='opentelemetry-exporter-otlp-proto-grpc'):
            tracermod._TelemetryTracer(exporters={'otlp'})


def test_telemetry_tracer_otlp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel-collector:4317')
    fake_module = types.SimpleNamespace(OTLPSpanExporter=MagicMock())
    with patch('dns_synchub.tracer.importlib.import_module', return_value=fake_module):
        tracer = tracermod._TelemetryTracer(exporters={'otlp'})
    assert tracer.tracer_provider is not None
    got = tracermod.get_tracer('dns_synchub.test')
    assert got is not None
