// Getting onto the network: Wi-Fi on the ESP32-S3; Ethernet (with PoE) or Wi-Fi (through the
// board's ESP32-C6) on the ESP32-P4 board. Calls `onIP` each time an address is assigned.
//
// On a board with both, the saved choice (Provisioning::loadNetworkUse) decides:
//   auto      Ethernet, or Wi-Fi if a network is saved and the cable has no link at startup
//   ethernet  Ethernet only
//   wifi      Wi-Fi only
#pragma once

#include "cJSON.h"

namespace Network {
	using IPCallback = void (*)();

	// Starts whatever this board has; false if there's nothing to start (no Wi-Fi network saved
	// on a Wi-Fi-only board).
	bool begin( const char *hostname, IPCallback onIP );

	// Adds "network": {"has", "use", "active", "ssid", "rssi"} for /info.
	void addInfo( cJSON *parent );
}
