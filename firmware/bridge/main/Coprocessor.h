// The ESP32-P4 board's Wi-Fi chip, an ESP32-C6 running ESP-Hosted: its firmware version, and
// updating that firmware from the P4 over their SDIO link (POST /coprocessor). Does nothing on
// boards without one.
#pragma once

#include "cJSON.h"
#include "esp_http_server.h"

namespace Coprocessor {
	// Asks the C6 for its firmware version, once the link to it is up (after esp_wifi_init).
	void readVersion();

	// Adds "coprocessor": "2.13.1" (or null while unknown) to /info's network object.
	void addInfo( cJSON *network );

	// Registers POST /coprocessor: the body is an ESP-Hosted co-processor image for the C6.
	void registerHandler( httpd_handle_t server );
}
