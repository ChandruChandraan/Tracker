"""Hardware serial communication and virtual controller simulation module."""

from .serial_manager import SerialDeviceManager, SerialPortReaderThread

__all__ = ["SerialDeviceManager", "SerialPortReaderThread"]
