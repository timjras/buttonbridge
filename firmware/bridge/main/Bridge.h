// The network side: an HTTP server with one WebSocket for the renderer, mDNS so the renderer
// can find this bridge, updates over Wi-Fi, and a task that uploads key images to the deck.
// See docs/BRIDGE-PROTOCOL.md.
#pragma once

#include <cstddef>

#include "StreamDeck.h"

namespace Bridge {
	// `usbHost` is false when a computer is on the native port: the deck then has no USB host
	// and reports no deck.
	bool begin( StreamDeck &deck, bool usbHost, const char *hostname, const char *mac );

	// Starts mDNS once there's an IP address (safe to call again).
	void networkUp();

	// Forwards a deck event to the renderer and keeps mDNS current.
	void handleDeckEvent( const StreamDeck::Event &event );

	// The JSON that GET /info returns and the WebSocket's hello carries.
	size_t infoJSON( char *out, size_t size );
}
