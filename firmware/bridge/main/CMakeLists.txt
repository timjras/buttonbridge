idf_component_register(SRCS "main.cpp" "StreamDeck.cpp" "Bridge.cpp" "Provisioning.cpp" "ImageCache.cpp" "Network.cpp" "Coprocessor.cpp" "LogBuffer.cpp"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_wifi esp_eth esp_netif nvs_flash esp_http_server app_update
                                     esp_driver_uart esp_driver_usb_serial_jtag esp_timer json
                                     esp_app_format usb mbedtls espcoredump spi_flash)
