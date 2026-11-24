# Patched copy of the ESP-IDF USB Host Library

This is `espressif/usb` 1.6.0 from the ESP Component Registry
(https://github.com/espressif/esp-usb/tree/master/host/usb, Apache License 2.0), with one change in
`src/hcd_dwc.c`: under `CONFIG_USB_HOST_HW_BUFFER_BIAS_PERIODIC_OUT` the RX FIFO gets 3 status
lines instead of 2, as esp-usb master already does. Without it, on the ESP32-P4's high-speed port, a
Stream Deck's 512-byte interrupt IN endpoint never completes and image uploads fail or crash the
host. Drop this folder once a released `espressif/usb` includes that fix.
