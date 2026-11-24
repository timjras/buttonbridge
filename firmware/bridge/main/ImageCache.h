// Key images kept in PSRAM by hash, so the renderer uploads each one once and then switches
// keys between them with a short "show" message. Least recently used images go first when
// it's full. Not kept across restarts: the hello lists what's cached.
#pragma once

#include <cstddef>
#include <cstdint>

#include "cJSON.h"

namespace ImageCache {
	constexpr size_t kHashSize = 16;   // first 16 bytes of the image's SHA-256

	void begin();

	// Stores an image (copies it) if its SHA-256 starts with `hash`; false if it doesn't.
	bool add( const uint8_t ( &hash )[kHashSize], const uint8_t *image, size_t length );

	// A new buffer of `prefix` uninitialized bytes followed by the image, for the upload queue;
	// nullptr if it isn't cached. The caller frees it with heap_caps_free().
	uint8_t *copy( const uint8_t ( &hash )[kHashSize], size_t prefix, size_t &length );

	// Adds a "cached" array of the cached hashes, as hex.
	void addHashes( cJSON *parent );

	void toHex( const uint8_t ( &hash )[kHashSize], char ( &out )[kHashSize * 2 + 1] );
	bool fromHex( const char *text, uint8_t ( &out )[kHashSize] );
}
