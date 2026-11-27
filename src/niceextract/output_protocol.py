"""Extraction wire protocol selection; legacy decoding is strictly offline."""

from .errors import ConfigurationError
from .models import OutputProtocol
from .backends import ReplayBackend as _ReplayBackend


def output_protocol_for_backend(backend: object) -> OutputProtocol:
    # A custom backend's mode/name/attribute must not select a legacy live wire.
    if type(backend) is _ReplayBackend:
        protocol = backend.output_protocol
        if protocol not in ("flat-v1", "native-v1"):
            raise ConfigurationError("Replay output_protocol must be flat-v1 or native-v1.")
        return protocol
    return "flat-v1"
