"""Package exports for the eb200 driver."""

from .device import EB200, DeviceInfo
from .errors import (
    EB200Connection,
    EB200Error,
    EB200Protocol,
    EB200Timeout,
    SCPIError,
)
from .sim import SimulatedTransport
from .streams import AudioReceiver
from .transport import TCPTransport

__version__ = "0.1.0"

__all__ = [
    "AudioReceiver",
    "DeviceInfo",
    "EB200",
    "EB200Connection",
    "EB200Error",
    "EB200Protocol",
    "EB200Timeout",
    "SCPIError",
    "SimulatedTransport",
    "TCPTransport",
    "__version__",
]
