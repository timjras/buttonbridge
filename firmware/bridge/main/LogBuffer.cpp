#include "LogBuffer.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"

namespace {
	constexpr size_t kSize = 32 * 1024;

	char          *buffer  = nullptr;
	size_t         head    = 0;       // where the next byte goes
	bool           wrapped = false;
	portMUX_TYPE   lock    = portMUX_INITIALIZER_UNLOCKED;
	vprintf_like_t console = nullptr;

	void append( const char *text, size_t length ) {
		taskENTER_CRITICAL( &lock );
		for( size_t i = 0; i < length; i++ ) {
			buffer[head] = text[i];
			head         = ( head + 1 ) % kSize;
			if( head == 0 )
				wrapped = true;
		}
		taskEXIT_CRITICAL( &lock );
	}

	int capture( const char *format, va_list args ) {
		char    line[256];
		va_list copy;
		va_copy( copy, args );
		int length = vsnprintf( line, sizeof( line ), format, copy );
		va_end( copy );
		if( length > 0 )
			append( line, length < (int)sizeof( line ) ? length : sizeof( line ) - 1 );
		return console ? console( format, args ) : length;
	}
}

void LogBuffer::begin() {
	buffer = (char *)heap_caps_malloc( kSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT );
	if( buffer )
		console = esp_log_set_vprintf( capture );
}

size_t LogBuffer::read( char *out, size_t size ) {
	if( !buffer || size == 0 )
		return 0;
	taskENTER_CRITICAL( &lock );
	size_t stored = wrapped ? kSize : head;
	size_t count  = stored < size - 1 ? stored : size - 1;
	size_t start  = ( head + kSize - count ) % kSize;
	for( size_t i = 0; i < count; i++ )
		out[i] = buffer[( start + i ) % kSize];
	taskEXIT_CRITICAL( &lock );
	out[count] = 0;
	return count;
}
