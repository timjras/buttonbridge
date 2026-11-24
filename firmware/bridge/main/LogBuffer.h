// Keeps the most recent log output in memory, so GET /log can show it over the network (a
// board in a wall has no cable to read logs from).
#pragma once

#include <cstddef>

namespace LogBuffer {
	// Starts copying every log line into the buffer, as well as to the console.
	void begin();

	// Copies the buffered text, oldest first, into out (at most size - 1 bytes plus a NUL).
	size_t read( char *out, size_t size );
}
