// Wi-Fi setup over the serial ports (UART/COM always, the native USB port while a computer
// is on it). tools/set_wifi.py sends one JSON line; replies are JSON lines starting "@@" so
// they can be picked out from the logs.
//
//   {"cmd":"wifi","ssid":"...","password":"..."}   saves the network and restarts
//   {"cmd":"network","use":"auto"}                  "auto", "ethernet" or "wifi" (see Network.h); restarts
//   {"cmd":"info"}                                   prints the same JSON as GET /info
#pragma once

#include <cstddef>

namespace Provisioning {
	// Fills `out` with the info JSON; returns its length.
	using InfoFunction = size_t (*)( char *out, size_t size );

	void begin( bool usbSerial, InfoFunction info );

	// The saved network; false if none has been set.
	bool loadWifi( char ( &ssid )[33], char ( &password )[65] );

	// The saved choice between Ethernet and Wi-Fi; leaves `use` alone if none has been set.
	void loadNetworkUse( char ( &use )[10] );
}
