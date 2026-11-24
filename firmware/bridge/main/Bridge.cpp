#include "Bridge.h"

#include <cstdio>
#include <cstring>
#include <unistd.h>

#include "Config.h"
#include "Coprocessor.h"
#include "ImageCache.h"
#include "LogBuffer.h"
#include "Network.h"
#include "cJSON.h"
#include "esp_core_dump.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mdns.h"

namespace {
	constexpr const char *TAG = "bridge";

	constexpr uint8_t  kOpSetKey      = 0x01;   // binary frame: [op][key][image...]
	constexpr uint8_t  kOpCache       = 0x02;   // binary frame: [op][16-byte hash][image...]
	constexpr uint8_t  kOpScreen      = 0x03;   // binary frame: [op][x][y][width][height][image...], each LE16
	constexpr size_t   kOTAChunk      = 4096;
	constexpr uint32_t kRestartWait   = 500;    // ms for a reply to go out before restarting

	StreamDeck       *deck       = nullptr;
	bool              usbHost    = false;
	httpd_handle_t    server     = nullptr;
	char              hostname[32];
	char              mac[18];
	bool              mdnsUp     = false;

	// The renderer's WebSocket; -1 when none. A new connection replaces the old one.
	SemaphoreHandle_t clientLock = nullptr;
	int               clientFD   = -1;

	// Images waiting to go to the deck, newest wins, and what each key last showed (replayed
	// when the deck is plugged back in). Each buffer is a whole WebSocket frame; the image
	// starts at kImageOffset.
	constexpr size_t  kImageOffset = 2;
	struct Image {
		uint8_t *frame  = nullptr;
		size_t   length = 0;
	};
	SemaphoreHandle_t imageLock = nullptr;
	Image             pending[kMaxKeys];
	Image             shown[kMaxKeys];
	uint16_t          shownPID  = 0;           // the deck `shown` was drawn for
	TaskHandle_t      uploader  = nullptr;

	// The same for the screen (the +'s touch strip), by the area each image covers: a new
	// image for an area replaces the waiting one. The renderer draws a few fixed areas (the
	// +'s strip in four, one above each dial), so a handful of slots is plenty.
	constexpr size_t  kScreenOffset = 9;
	constexpr size_t  kScreenSlots  = 8;
	struct Area {
		uint16_t x = 0, y = 0, width = 0, height = 0;
		bool operator==( const Area &other ) const {
			return x == other.x && y == other.y && width == other.width && height == other.height;
		}
	};
	struct ScreenImage {
		Area  area;
		Image image;
	};
	ScreenImage       pendingScreen[kScreenSlots];
	ScreenImage       shownScreen[kScreenSlots];

	void freeImage( Image &image ) {
		heap_caps_free( image.frame );
		image = {};
	}

	// Puts an image in the slot for its area, or a free one; takes ownership of it. Caller
	// holds imageLock. False (and the image freed) if every slot holds another area.
	bool storeScreen( ScreenImage ( &slots )[kScreenSlots], const Area &area, const Image &image ) {
		ScreenImage *slot = nullptr;
		for( ScreenImage &s : slots ) {
			if( s.image.frame && s.area == area ) {
				slot = &s;
				break;
			}
			if( !s.image.frame && !slot )
				slot = &s;
		}
		if( !slot ) {
			Image dropped = image;
			freeImage( dropped );
			return false;
		}
		freeImage( slot->image );
		*slot = { area, image };
		return true;
	}

	uint16_t le16( const uint8_t *p ) {
		return p[0] | ( p[1] << 8 );
	}

	// MARK: - JSON

	void addDeck( cJSON *parent, const StreamDeck::Info &info ) {
		cJSON *d = cJSON_AddObjectToObject( parent, "deck" );
		cJSON_AddStringToObject( d, "model", info.model );
		cJSON_AddNumberToObject( d, "pid", info.pid );
		cJSON_AddNumberToObject( d, "rows", info.rows );
		cJSON_AddNumberToObject( d, "cols", info.cols );
		cJSON_AddNumberToObject( d, "keys", info.keyCount() );
		cJSON_AddNumberToObject( d, "keySize", info.keySize );
		cJSON_AddStringToObject( d, "format", StreamDeck::formatName( info.format ) );
		cJSON_AddStringToObject( d, "transform", StreamDeck::transformName( info.transform ) );
		cJSON_AddStringToObject( d, "serial", info.serial );
		cJSON_AddStringToObject( d, "firmware", info.firmware );
		cJSON_AddNumberToObject( d, "dials", info.dials );
		if( info.screenWidth ) {
			cJSON *screen = cJSON_AddObjectToObject( d, "screen" );
			cJSON_AddNumberToObject( screen, "width", info.screenWidth );
			cJSON_AddNumberToObject( screen, "height", info.screenHeight );
			cJSON_AddStringToObject( screen, "transform", StreamDeck::transformName( info.screenTransform ) );
		} else {
			cJSON_AddNullToObject( d, "screen" );
		}
	}

	void addDeckOrNull( cJSON *parent ) {
		if( deck && deck->isConnected() )
			addDeck( parent, deck->info() );
		else
			cJSON_AddNullToObject( parent, "deck" );
	}

	cJSON *infoObject( const char *type ) {
		cJSON *root = cJSON_CreateObject();
		if( type )
			cJSON_AddStringToObject( root, "type", type );
		cJSON_AddNumberToObject( root, "protocol", kProtocolVersion );
		cJSON_AddStringToObject( root, "firmware", firmwareVersion() );
		cJSON_AddStringToObject( root, "hostname", hostname );
		cJSON_AddStringToObject( root, "mac", mac );
		cJSON_AddNumberToObject( root, "maxImage", kMaxImageSize );
		cJSON_AddNumberToObject( root, "resetReason", esp_reset_reason() );
		cJSON_AddNumberToObject( root, "uptime", (double)( esp_timer_get_time() / 1000000 ) );
		addDeckOrNull( root );
		Network::addInfo( root );
		ImageCache::addHashes( root );

		// What the native USB port is doing, for diagnosing a deck that doesn't show up.
		cJSON *usb = cJSON_AddObjectToObject( root, "usb" );
		cJSON_AddStringToObject( usb, "mode", usbHost ? "host" : "computer" );
		StreamDeck::UsbDevice last = usbHost ? deck->lastUsbDevice() : StreamDeck::UsbDevice {};
		if( last.seen ) {
			char id[10];
			snprintf( id, sizeof( id ), "%04x:%04x", last.vid, last.pid );
			cJSON_AddStringToObject( usb, "lastDevice", id );
			cJSON_AddNumberToObject( usb, "lastDeviceClass", last.deviceClass );
		} else {
			cJSON_AddNullToObject( usb, "lastDevice" );
		}
		return root;
	}

	// MARK: - Sending to the renderer

	struct Outgoing {
		int   fd;
		char *text;
	};

	void sendQueued( void *arg ) {
		Outgoing     *out   = (Outgoing *)arg;
		httpd_ws_frame_t frame = {};
		frame.type    = HTTPD_WS_TYPE_TEXT;
		frame.payload = (uint8_t *)out->text;
		frame.len     = strlen( out->text );
		if( httpd_ws_get_fd_info( server, out->fd ) == HTTPD_WS_CLIENT_WEBSOCKET )
			httpd_ws_send_frame_async( server, out->fd, &frame );
		free( out->text );
		free( out );
	}

	// Sends JSON to the renderer, if one is connected. Takes ownership of `json`.
	void sendToRenderer( cJSON *json, int fd = -1 ) {
		if( fd < 0 ) {
			xSemaphoreTake( clientLock, portMAX_DELAY );
			fd = clientFD;
			xSemaphoreGive( clientLock );
		}
		char *text = cJSON_PrintUnformatted( json );
		cJSON_Delete( json );
		if( fd < 0 || !text ) {
			free( text );
			return;
		}
		Outgoing *out = (Outgoing *)malloc( sizeof( Outgoing ) );
		out->fd   = fd;
		out->text = text;
		if( httpd_queue_work( server, sendQueued, out ) != ESP_OK ) {
			free( text );
			free( out );
		}
	}

	// MARK: - Uploading key images

	// Tells the renderer an image is drawn and how long the deck took.
	void reportDrawn( cJSON *drawn, esp_err_t err, int64_t started ) {
		cJSON_AddStringToObject( drawn, "type", "drawn" );
		cJSON_AddBoolToObject( drawn, "ok", err == ESP_OK );
		if( err != ESP_OK )
			cJSON_AddStringToObject( drawn, "error", esp_err_to_name( err ) );
		cJSON_AddNumberToObject( drawn, "ms", (double)( ( esp_timer_get_time() - started ) / 1000 ) );
		sendToRenderer( drawn );
	}

	// Draws the lowest key with something waiting; false if none is.
	bool uploadKey() {
		Image   image;
		uint8_t key = 0;
		xSemaphoreTake( imageLock, portMAX_DELAY );
		for( ; key < kMaxKeys && !pending[key].frame; key++ ) {}
		if( key < kMaxKeys ) {
			image        = pending[key];
			pending[key] = {};
		}
		xSemaphoreGive( imageLock );
		if( key == kMaxKeys )
			return false;

		int64_t   started = esp_timer_get_time();
		esp_err_t err     = deck->setKeyImage( key, image.frame + kImageOffset, image.length - kImageOffset );
		if( err != ESP_OK )
			ESP_LOGW( TAG, "Key %u image: %s", key, esp_err_to_name( err ) );
		cJSON *drawn = cJSON_CreateObject();
		cJSON_AddNumberToObject( drawn, "key", key );
		reportDrawn( drawn, err, started );

		xSemaphoreTake( imageLock, portMAX_DELAY );
		freeImage( shown[key] );
		shown[key] = image;
		shownPID   = deck->info().pid;
		xSemaphoreGive( imageLock );
		return true;
	}

	// Draws one waiting screen image; false if none is.
	bool uploadScreen() {
		ScreenImage next;
		xSemaphoreTake( imageLock, portMAX_DELAY );
		for( ScreenImage &slot : pendingScreen ) {
			if( slot.image.frame ) {
				next = slot;
				slot = {};
				break;
			}
		}
		xSemaphoreGive( imageLock );
		if( !next.image.frame )
			return false;

		const Area &a       = next.area;
		int64_t     started = esp_timer_get_time();
		esp_err_t   err     = deck->setScreenImage( a.x, a.y, a.width, a.height, next.image.frame + kScreenOffset, next.image.length - kScreenOffset );
		if( err != ESP_OK )
			ESP_LOGW( TAG, "Screen image at %u, %u: %s", a.x, a.y, esp_err_to_name( err ) );
		cJSON *drawn = cJSON_CreateObject();
		cJSON_AddBoolToObject( drawn, "screen", true );
		cJSON_AddNumberToObject( drawn, "x", a.x );
		cJSON_AddNumberToObject( drawn, "y", a.y );
		reportDrawn( drawn, err, started );

		xSemaphoreTake( imageLock, portMAX_DELAY );
		storeScreen( shownScreen, a, next.image );
		shownPID = deck->info().pid;
		xSemaphoreGive( imageLock );
		return true;
	}

	void uploadTask( void * ) {
		for( ;; ) {
			ulTaskNotifyTake( pdTRUE, portMAX_DELAY );
			// Keys first, then the screen, until nothing waits.
			while( uploadKey() || uploadScreen() ) {}
		}
	}

	// Queues a frame from the renderer; takes ownership of it.
	void queueImage( uint8_t key, uint8_t *frame, size_t length ) {
		xSemaphoreTake( imageLock, portMAX_DELAY );
		freeImage( pending[key] );
		pending[key] = { frame, length };
		xSemaphoreGive( imageLock );
		xTaskNotifyGive( uploader );
	}

	// Queues a screen frame from the renderer; takes ownership of it.
	void queueScreen( const Area &area, uint8_t *frame, size_t length ) {
		xSemaphoreTake( imageLock, portMAX_DELAY );
		bool queued = storeScreen( pendingScreen, area, { frame, length } );
		xSemaphoreGive( imageLock );
		if( queued )
			xTaskNotifyGive( uploader );
		else
			ESP_LOGW( TAG, "Too many screen areas waiting; dropped the image at %u, %u", area.x, area.y );
	}

	// The same deck model came back: show what it showed before, unless something newer waits.
	void replayShown( uint16_t pid ) {
		xSemaphoreTake( imageLock, portMAX_DELAY );
		bool any = false;
		if( pid == shownPID ) {
			for( uint8_t key = 0; key < kMaxKeys; key++ ) {
				if( shown[key].frame && !pending[key].frame ) {
					pending[key] = shown[key];
					shown[key]   = {};
					any          = true;
				}
			}
			for( ScreenImage &old : shownScreen ) {
				if( !old.image.frame )
					continue;
				bool newer = false;
				for( const ScreenImage &waiting : pendingScreen )
					newer = newer || ( waiting.image.frame && waiting.area == old.area );
				if( newer )
					freeImage( old.image );
				else
					any = storeScreen( pendingScreen, old.area, old.image ) || any;
				old = {};
			}
		} else {
			for( auto &image : shown )
				freeImage( image );
			for( ScreenImage &old : shownScreen ) {
				freeImage( old.image );
				old = {};
			}
		}
		xSemaphoreGive( imageLock );
		if( any )
			xTaskNotifyGive( uploader );
	}

	// MARK: - mDNS

	void updateTXT() {
		if( !mdnsUp )
			return;
		StreamDeck::Info info = deck && deck->isConnected() ? deck->info() : StreamDeck::Info {};
		char keys[8];
		snprintf( keys, sizeof( keys ), "%u", info.keyCount() );
		mdns_service_txt_item_set( kServiceType, kServiceProto, "model", info.pid ? info.model : "none" );
		mdns_service_txt_item_set( kServiceType, kServiceProto, "keys", keys );
	}

	// MARK: - HTTP handlers

	esp_err_t infoHandler( httpd_req_t *req ) {
		char *text = cJSON_PrintUnformatted( infoObject( nullptr ) );
		httpd_resp_set_type( req, "application/json" );
		httpd_resp_sendstr( req, text );
		free( text );
		return ESP_OK;
	}

	uint8_t keyCount() {
		return deck && deck->isConnected() ? deck->info().keyCount() : 0;
	}

	// {"type":"show","key":3,"hash":"..."}: puts a cached image on a key.
	void showCached( const cJSON *keyItem, const cJSON *hashItem ) {
		uint8_t hash[ImageCache::kHashSize];
		if( !cJSON_IsNumber( keyItem ) || !cJSON_IsString( hashItem ) || !ImageCache::fromHex( hashItem->valuestring, hash ) )
			return;
		int key = keyItem->valueint;
		if( key < 0 || key >= keyCount() )
			return;
		size_t   length = 0;
		uint8_t *frame  = ImageCache::copy( hash, kImageOffset, length );
		if( frame ) {
			queueImage( (uint8_t)key, frame, length );
			return;
		}
		cJSON *missing = cJSON_CreateObject();
		cJSON_AddStringToObject( missing, "type", "missing" );
		cJSON_AddNumberToObject( missing, "key", key );
		cJSON_AddStringToObject( missing, "hash", hashItem->valuestring );
		sendToRenderer( missing );
	}

	esp_err_t logHandler( httpd_req_t *req ) {
		constexpr size_t kMax = 32 * 1024;
		char *text = (char *)heap_caps_malloc( kMax, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT );
		if( !text )
			return ESP_ERR_NO_MEM;
		size_t length = LogBuffer::read( text, kMax );
		httpd_resp_set_type( req, "text/plain; charset=utf-8" );
		httpd_resp_send( req, text, length );
		heap_caps_free( text );
		return ESP_OK;
	}

	// The crash dump from the last panic, raw; read it with
	//   esp-coredump info_corefile -t raw -c dump.bin build/sdbridge.elf
	esp_err_t coredumpHandler( httpd_req_t *req ) {
		size_t address = 0, size = 0;
		if( esp_core_dump_image_get( &address, &size ) != ESP_OK ) {
			httpd_resp_send_err( req, HTTPD_404_NOT_FOUND, "No crash dump" );
			return ESP_OK;
		}
		httpd_resp_set_type( req, "application/octet-stream" );
		char chunk[1024];
		for( size_t offset = 0; offset < size; offset += sizeof( chunk ) ) {
			size_t piece = size - offset < sizeof( chunk ) ? size - offset : sizeof( chunk );
			if( esp_flash_read( nullptr, chunk, address + offset, piece ) != ESP_OK ||
			    httpd_resp_send_chunk( req, chunk, piece ) != ESP_OK )
				return ESP_FAIL;
		}
		httpd_resp_send_chunk( req, nullptr, 0 );
		return ESP_OK;
	}

	void handleText( const char *text ) {
		cJSON *json = cJSON_Parse( text );
		if( !json )
			return;
		const cJSON *type = cJSON_GetObjectItem( json, "type" );
		if( cJSON_IsString( type ) ) {
			if( strcmp( type->valuestring, "brightness" ) == 0 ) {
				const cJSON *percent = cJSON_GetObjectItem( json, "percent" );
				if( cJSON_IsNumber( percent ) && deck && deck->isConnected() ) {
					int value = percent->valueint < 0 ? 0 : percent->valueint > 100 ? 100 : percent->valueint;
					deck->setBrightness( (uint8_t)value );
				}
			} else if( strcmp( type->valuestring, "show" ) == 0 ) {
				showCached( cJSON_GetObjectItem( json, "key" ), cJSON_GetObjectItem( json, "hash" ) );
			} else if( strcmp( type->valuestring, "ping" ) == 0 ) {
				cJSON *pong = cJSON_CreateObject();
				cJSON_AddStringToObject( pong, "type", "pong" );
				sendToRenderer( pong );
			}
		}
		cJSON_Delete( json );
	}

	// After the WebSocket handshake: this connection is now the renderer.
	esp_err_t wsConnected( httpd_req_t *req ) {
		int fd = httpd_req_to_sockfd( req );
		xSemaphoreTake( clientLock, portMAX_DELAY );
		int previous = clientFD;
		clientFD     = fd;
		xSemaphoreGive( clientLock );
		if( previous >= 0 && previous != fd ) {
			ESP_LOGI( TAG, "New renderer connection replaces the old one" );
			httpd_sess_trigger_close( server, previous );
		}
		ESP_LOGI( TAG, "Renderer connected" );
		sendToRenderer( infoObject( "hello" ), fd );
		return ESP_OK;
	}

	esp_err_t wsHandler( httpd_req_t *req ) {
		if( req->method == HTTP_GET )
			return ESP_OK;   // the handshake; see wsConnected()

		httpd_ws_frame_t frame = {};
		esp_err_t        err   = httpd_ws_recv_frame( req, &frame, 0 );   // the length only
		if( err != ESP_OK )
			return err;
		if( frame.len > kMaxImageSize + 1 + ImageCache::kHashSize ) {
			ESP_LOGW( TAG, "Frame of %u bytes is too big", (unsigned)frame.len );
			return ESP_FAIL;   // closes the connection
		}
		uint8_t *buffer = (uint8_t *)heap_caps_malloc( frame.len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT );
		if( !buffer )
			return ESP_ERR_NO_MEM;
		frame.payload = buffer;
		err = httpd_ws_recv_frame( req, &frame, frame.len );
		if( err != ESP_OK ) {
			heap_caps_free( buffer );
			return err;
		}

		if( frame.type == HTTPD_WS_TYPE_BINARY && frame.len > kImageOffset && buffer[0] == kOpSetKey ) {
			uint8_t key   = buffer[1];
			uint8_t count = keyCount();
			if( key < count ) {
				queueImage( key, buffer, frame.len );
				return ESP_OK;
			}
			ESP_LOGW( TAG, "Image for key %u, but the deck has %u keys", key, count );
		} else if( frame.type == HTTPD_WS_TYPE_BINARY && frame.len > kScreenOffset && buffer[0] == kOpScreen ) {
			Area area = { le16( buffer + 1 ), le16( buffer + 3 ), le16( buffer + 5 ), le16( buffer + 7 ) };
			StreamDeck::Info info = deck && deck->isConnected() ? deck->info() : StreamDeck::Info {};
			if( area.width && area.height && area.x + area.width <= info.screenWidth && area.y + area.height <= info.screenHeight ) {
				queueScreen( area, buffer, frame.len );
				return ESP_OK;
			}
			ESP_LOGW( TAG, "Screen image %u × %u at %u, %u doesn't fit the deck's screen", area.width, area.height, area.x, area.y );
		} else if( frame.type == HTTPD_WS_TYPE_BINARY && frame.len > 1 + ImageCache::kHashSize && buffer[0] == kOpCache ) {
			uint8_t hash[ImageCache::kHashSize];
			memcpy( hash, buffer + 1, sizeof( hash ) );
			bool ok = ImageCache::add( hash, buffer + 1 + sizeof( hash ), frame.len - 1 - sizeof( hash ) );
			char hex[ImageCache::kHashSize * 2 + 1];
			ImageCache::toHex( hash, hex );
			cJSON *reply = cJSON_CreateObject();
			cJSON_AddStringToObject( reply, "type", "cached" );
			cJSON_AddStringToObject( reply, "hash", hex );
			cJSON_AddBoolToObject( reply, "ok", ok );
			sendToRenderer( reply );
		} else if( frame.type == HTTPD_WS_TYPE_TEXT ) {
			buffer[frame.len] = 0;
			handleText( (const char *)buffer );
		}
		heap_caps_free( buffer );
		return ESP_OK;
	}

	// POST /update with the app image (build/sdbridge.bin) as the body.
	esp_err_t updateHandler( httpd_req_t *req ) {
		const esp_partition_t *target = esp_ota_get_next_update_partition( nullptr );
		if( !target || req->content_len == 0 || req->content_len > target->size ) {
			httpd_resp_send_err( req, HTTPD_400_BAD_REQUEST, "No room for that image" );
			return ESP_FAIL;
		}
		ESP_LOGI( TAG, "Update: receiving %u bytes into %s", (unsigned)req->content_len, target->label );

		esp_ota_handle_t ota;
		esp_err_t        err = esp_ota_begin( target, req->content_len, &ota );
		char            *chunk = (char *)malloc( kOTAChunk );
		size_t           remaining = req->content_len;
		while( err == ESP_OK && remaining > 0 ) {
			int got = httpd_req_recv( req, chunk, remaining < kOTAChunk ? remaining : kOTAChunk );
			if( got == HTTPD_SOCK_ERR_TIMEOUT )
				continue;
			if( got <= 0 ) {
				err = ESP_FAIL;
				break;
			}
			err        = esp_ota_write( ota, chunk, got );
			remaining -= got;
		}
		free( chunk );
		if( err == ESP_OK )
			err = esp_ota_end( ota );
		else
			esp_ota_abort( ota );
		if( err == ESP_OK )
			err = esp_ota_set_boot_partition( target );

		if( err != ESP_OK ) {
			ESP_LOGE( TAG, "Update failed: %s", esp_err_to_name( err ) );
			httpd_resp_send_err( req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name( err ) );
			return ESP_FAIL;
		}
		ESP_LOGI( TAG, "Update installed; restarting" );
		httpd_resp_set_type( req, "application/json" );
		httpd_resp_sendstr( req, "{\"ok\":true,\"restarting\":true}" );
		vTaskDelay( pdMS_TO_TICKS( kRestartWait ) );
		esp_restart();
		return ESP_OK;
	}

	void closeHandler( httpd_handle_t, int fd ) {
		xSemaphoreTake( clientLock, portMAX_DELAY );
		if( clientFD == fd ) {
			clientFD = -1;
			ESP_LOGI( TAG, "Renderer disconnected" );
		}
		xSemaphoreGive( clientLock );
		close( fd );
	}
}

bool Bridge::begin( StreamDeck &theDeck, bool theUsbHost, const char *theHostname, const char *theMAC ) {
	deck    = &theDeck;
	usbHost = theUsbHost;
	strlcpy( hostname, theHostname, sizeof( hostname ) );
	strlcpy( mac, theMAC, sizeof( mac ) );
	clientLock = xSemaphoreCreateMutex();
	imageLock  = xSemaphoreCreateMutex();
	ImageCache::begin();
	xTaskCreate( uploadTask, "upload", 4096, nullptr, 3, &uploader );

	httpd_config_t config   = HTTPD_DEFAULT_CONFIG();
	config.server_port      = kHttpPort;
	config.close_fn         = closeHandler;
	config.recv_wait_timeout = 10;
	config.stack_size       = 8192;
	config.lru_purge_enable = true;
	if( httpd_start( &server, &config ) != ESP_OK ) {
		ESP_LOGE( TAG, "Couldn't start the HTTP server" );
		return false;
	}

	httpd_uri_t info   = { .uri = "/info", .method = HTTP_GET, .handler = infoHandler, .user_ctx = nullptr };
	httpd_uri_t ws     = { .uri = "/ws", .method = HTTP_GET, .handler = wsHandler, .user_ctx = nullptr,
	                       .is_websocket = true, .ws_post_handshake_cb = wsConnected };
	httpd_uri_t update = { .uri = "/update", .method = HTTP_POST, .handler = updateHandler, .user_ctx = nullptr };
	httpd_register_uri_handler( server, &info );
	httpd_register_uri_handler( server, &ws );
	httpd_register_uri_handler( server, &update );
	httpd_uri_t logs = { .uri = "/log", .method = HTTP_GET, .handler = logHandler, .user_ctx = nullptr };
	httpd_register_uri_handler( server, &logs );
	httpd_uri_t coredump = { .uri = "/coredump", .method = HTTP_GET, .handler = coredumpHandler, .user_ctx = nullptr };
	httpd_register_uri_handler( server, &coredump );
	Coprocessor::registerHandler( server );
	return true;
}

void Bridge::networkUp() {
	if( mdnsUp )
		return;
	if( mdns_init() != ESP_OK ) {
		ESP_LOGE( TAG, "mDNS didn't start" );
		return;
	}
	mdns_hostname_set( hostname );
	mdns_instance_name_set( hostname );
	char protocol[8];
	snprintf( protocol, sizeof( protocol ), "%d", kProtocolVersion );
	mdns_txt_item_t txt[] = {
		{ "protocol", protocol },
		{ "fw", firmwareVersion() },
		{ "mac", mac },
	};
	mdns_service_add( hostname, kServiceType, kServiceProto, kHttpPort, txt, sizeof( txt ) / sizeof( txt[0] ) );
	mdnsUp = true;
	updateTXT();
	ESP_LOGI( TAG, "Advertising %s.%s.%s on mDNS as %s.local", hostname, kServiceType, kServiceProto, hostname );
}

void Bridge::handleDeckEvent( const StreamDeck::Event &event ) {
	cJSON *json = cJSON_CreateObject();
	switch( event.type ) {
	case StreamDeck::EventType::KeyDown:
	case StreamDeck::EventType::KeyUp:
		cJSON_AddStringToObject( json, "type", "key" );
		cJSON_AddNumberToObject( json, "key", event.key );
		cJSON_AddBoolToObject( json, "down", event.type == StreamDeck::EventType::KeyDown );
		break;
	case StreamDeck::EventType::DialTurn:
		cJSON_AddStringToObject( json, "type", "dial" );
		cJSON_AddNumberToObject( json, "dial", event.key );
		cJSON_AddNumberToObject( json, "turn", event.delta );
		break;
	case StreamDeck::EventType::DialDown:
	case StreamDeck::EventType::DialUp:
		cJSON_AddStringToObject( json, "type", "dial" );
		cJSON_AddNumberToObject( json, "dial", event.key );
		cJSON_AddBoolToObject( json, "down", event.type == StreamDeck::EventType::DialDown );
		break;
	case StreamDeck::EventType::Touch:
		cJSON_AddStringToObject( json, "type", "touch" );
		cJSON_AddStringToObject( json, "kind", event.touch == StreamDeck::TouchKind::Swipe ? "swipe" : event.touch == StreamDeck::TouchKind::Long ? "long" : "tap" );
		cJSON_AddNumberToObject( json, "x", event.x );
		cJSON_AddNumberToObject( json, "y", event.y );
		if( event.touch == StreamDeck::TouchKind::Swipe ) {
			cJSON_AddNumberToObject( json, "toX", event.toX );
			cJSON_AddNumberToObject( json, "toY", event.toY );
		}
		break;
	case StreamDeck::EventType::Connected: {
		StreamDeck::Info info = deck->info();
		ESP_LOGI( TAG, "Deck: %s, %u x %u keys of %u px, %s", info.model, info.cols, info.rows, info.keySize,
		          StreamDeck::formatName( info.format ) );
		cJSON_AddStringToObject( json, "type", "deck" );
		cJSON_AddBoolToObject( json, "connected", true );
		addDeck( json, info );
		replayShown( info.pid );
		updateTXT();
		break;
	}
	case StreamDeck::EventType::Disconnected:
		ESP_LOGI( TAG, "Deck unplugged" );
		cJSON_AddStringToObject( json, "type", "deck" );
		cJSON_AddBoolToObject( json, "connected", false );
		updateTXT();
		break;
	case StreamDeck::EventType::UsbDevice: {
		StreamDeck::UsbDevice usb = deck->lastUsbDevice();
		ESP_LOGI( TAG, "USB device %04x:%04x plugged in", usb.vid, usb.pid );
		cJSON_Delete( json );
		return;
	}
	}
	sendToRenderer( json );
}

size_t Bridge::infoJSON( char *out, size_t size ) {
	cJSON *json = infoObject( nullptr );
	bool   ok   = cJSON_PrintPreallocated( json, out, (int)size, false );
	cJSON_Delete( json );
	if( !ok && size )
		out[0] = 0;
	return strlen( out );
}
