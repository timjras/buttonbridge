#include "ImageCache.h"

#include <cstring>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "mbedtls/sha256.h"

namespace {
	constexpr const char *TAG        = "cache";
	constexpr size_t      kMaxImages = 256;
	constexpr size_t      kMaxBytes  = 4 * 1024 * 1024;   // of the 8 MB of PSRAM

	struct Entry {
		uint8_t  hash[ImageCache::kHashSize];
		uint8_t *data     = nullptr;
		size_t   length   = 0;
		uint32_t lastUsed = 0;
	};

	SemaphoreHandle_t lock  = nullptr;
	Entry             entries[kMaxImages];
	size_t            bytes = 0;
	uint32_t          clock = 0;

	// Caller holds lock.
	Entry *find( const uint8_t *hash ) {
		for( auto &entry : entries )
			if( entry.data && memcmp( entry.hash, hash, ImageCache::kHashSize ) == 0 )
				return &entry;
		return nullptr;
	}

	void evict( Entry &entry ) {
		bytes -= entry.length;
		heap_caps_free( entry.data );
		entry = {};
	}

	// The least recently used entry, or an empty one. Caller holds lock.
	Entry *oldest() {
		Entry *found = nullptr;
		for( auto &entry : entries ) {
			if( !entry.data )
				return &entry;
			if( !found || entry.lastUsed < found->lastUsed )
				found = &entry;
		}
		return found;
	}
}

void ImageCache::begin() {
	lock = xSemaphoreCreateMutex();
}

bool ImageCache::add( const uint8_t ( &hash )[kHashSize], const uint8_t *image, size_t length ) {
	uint8_t digest[32];
	mbedtls_sha256( image, length, digest, 0 );
	if( memcmp( digest, hash, kHashSize ) != 0 )
		return false;

	xSemaphoreTake( lock, portMAX_DELAY );
	Entry *entry = find( hash );
	if( !entry ) {
		uint8_t *data = (uint8_t *)heap_caps_malloc( length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT );
		if( data ) {
			memcpy( data, image, length );
			entry = oldest();
			if( entry->data )
				evict( *entry );
			while( bytes + length > kMaxBytes ) {
				Entry *old = oldest();
				if( !old->data )
					break;
				evict( *old );
			}
			memcpy( entry->hash, hash, kHashSize );
			entry->data   = data;
			entry->length = length;
			bytes        += length;
		} else {
			ESP_LOGW( TAG, "No memory for a %u-byte image", (unsigned)length );
		}
	}
	if( entry )
		entry->lastUsed = ++clock;
	xSemaphoreGive( lock );
	return entry != nullptr;
}

uint8_t *ImageCache::copy( const uint8_t ( &hash )[kHashSize], size_t prefix, size_t &length ) {
	uint8_t *out = nullptr;
	xSemaphoreTake( lock, portMAX_DELAY );
	Entry *entry = find( hash );
	if( entry ) {
		out = (uint8_t *)heap_caps_malloc( prefix + entry->length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT );
		if( out ) {
			memcpy( out + prefix, entry->data, entry->length );
			length          = prefix + entry->length;
			entry->lastUsed = ++clock;
		}
	}
	xSemaphoreGive( lock );
	return out;
}

void ImageCache::addHashes( cJSON *parent ) {
	cJSON *array = cJSON_AddArrayToObject( parent, "cached" );
	xSemaphoreTake( lock, portMAX_DELAY );
	for( auto &entry : entries ) {
		if( entry.data ) {
			char hex[kHashSize * 2 + 1];
			toHex( entry.hash, hex );
			cJSON_AddItemToArray( array, cJSON_CreateString( hex ) );
		}
	}
	xSemaphoreGive( lock );
}

void ImageCache::toHex( const uint8_t ( &hash )[kHashSize], char ( &out )[kHashSize * 2 + 1] ) {
	static const char digits[] = "0123456789abcdef";
	for( size_t i = 0; i < kHashSize; i++ ) {
		out[i * 2]     = digits[hash[i] >> 4];
		out[i * 2 + 1] = digits[hash[i] & 0x0F];
	}
	out[kHashSize * 2] = 0;
}

bool ImageCache::fromHex( const char *text, uint8_t ( &out )[kHashSize] ) {
	if( !text || strlen( text ) != kHashSize * 2 )
		return false;
	for( size_t i = 0; i < kHashSize * 2; i++ ) {
		char    c = text[i];
		uint8_t v = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : 0xFF;
		if( v == 0xFF )
			return false;
		out[i / 2] = i % 2 ? ( out[i / 2] | v ) : (uint8_t)( v << 4 );
	}
	return true;
}
