// Constants shared across the bridge firmware. See docs/BRIDGE-PROTOCOL.md for the wire format.
#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_app_desc.h"

inline const char *firmwareVersion() {
	return esp_app_get_description()->version;
}

constexpr int      kProtocolVersion = 3;

// mDNS service the renderer browses for.
constexpr const char *kServiceType  = "_sdbridge";
constexpr const char *kServiceProto = "_tcp";
constexpr uint16_t    kHttpPort     = 80;

constexpr uint8_t  kMaxKeys         = 32;          // Stream Deck XL (StreamDeck.h needs this)
constexpr uint8_t  kMaxDials        = 4;           // Stream Deck +
constexpr size_t   kMaxImageSize    = 64 * 1024;   // 96 px BMPs are ~27 KB, JPEGs far less
constexpr uint32_t kWifiRetryMs     = 5000;
constexpr uint32_t kEthernetLinkWaitMs = 6000;   // "auto": how long a cable gets before Wi-Fi takes over
