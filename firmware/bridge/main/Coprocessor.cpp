#include "Coprocessor.h"

#include <cinttypes>
#include <cstdio>
#include <cstdlib>

#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#if CONFIG_ESP_HOSTED_ENABLED
#include "esp_hosted.h"
#endif

namespace {
	constexpr const char *TAG = "coprocessor";

#if CONFIG_ESP_HOSTED_ENABLED
	constexpr size_t   kChunk       = 1480;   // what ESP-Hosted's own OTA examples send per RPC
	constexpr uint32_t kRestartWait = 500;    // ms for the reply to go out

	char version[24] = "";

	bool supportsActivate( const esp_hosted_coprocessor_fwver_t &v ) {
		return v.major1 > 2 || ( v.major1 == 2 && v.minor1 > 5 );   // esp_hosted_slave_ota_activate: 2.6.0+
	}

	esp_err_t fail( httpd_req_t *req, const char *step, esp_err_t err ) {
		ESP_LOGE( TAG, "Update failed at %s: %s", step, esp_err_to_name( err ) );
		char message[64];
		snprintf( message, sizeof( message ), "%s: %s", step, esp_err_to_name( err ) );
		httpd_resp_send_err( req, HTTPD_500_INTERNAL_SERVER_ERROR, message );
		return ESP_FAIL;
	}

	// The C6 writes the image to its other app slot and only boots it once it's complete, so a
	// failed or cut-off upload leaves its current firmware in place.
	esp_err_t updateHandler( httpd_req_t *req ) {
		if( req->content_len == 0 || req->content_len > 1920 * 1024 ) {   // the C6's app slots are 1920 KB
			httpd_resp_send_err( req, HTTPD_400_BAD_REQUEST, "Expected a co-processor image of up to 1920 KB" );
			return ESP_FAIL;
		}
		ESP_LOGI( TAG, "Update: sending %u bytes to the C6", (unsigned)req->content_len );

		esp_err_t err = esp_hosted_connect_to_slave() == ESP_OK ? ESP_OK : ESP_FAIL;   // no-op if already up
		if( err != ESP_OK )
			return fail( req, "connecting", err );
		esp_hosted_coprocessor_fwver_t before = {};
		esp_hosted_get_coprocessor_fwversion( &before );
		if( ( err = esp_hosted_slave_ota_begin() ) != ESP_OK )
			return fail( req, "begin", err );

		uint8_t *chunk     = (uint8_t *)malloc( kChunk );
		size_t   remaining = req->content_len;
		while( err == ESP_OK && remaining > 0 ) {
			int got = httpd_req_recv( req, (char *)chunk, remaining < kChunk ? remaining : kChunk );
			if( got == HTTPD_SOCK_ERR_TIMEOUT )
				continue;
			if( got <= 0 ) {
				err = ESP_FAIL;
				break;
			}
			err        = esp_hosted_slave_ota_write( chunk, got );
			remaining -= got;
		}
		free( chunk );
		if( err != ESP_OK )
			return fail( req, "write", err );
		if( ( err = esp_hosted_slave_ota_end() ) != ESP_OK )
			return fail( req, "end", err );

		// Firmware from 2.6 on waits to be told to switch; older firmware switches on the next
		// reset, which the P4 does to the C6 as it starts.
		if( supportsActivate( before ) && ( err = esp_hosted_slave_ota_activate() ) != ESP_OK )
			return fail( req, "activate", err );

		ESP_LOGI( TAG, "Update sent (was %" PRIu32 ".%" PRIu32 ".%" PRIu32 "); restarting", before.major1,
		          before.minor1, before.patch1 );
		httpd_resp_set_type( req, "application/json" );
		httpd_resp_sendstr( req, "{\"ok\":true,\"restarting\":true}" );
		vTaskDelay( pdMS_TO_TICKS( kRestartWait ) );
		esp_restart();
		return ESP_OK;
	}
#endif
}

void Coprocessor::readVersion() {
#if CONFIG_ESP_HOSTED_ENABLED
	esp_hosted_coprocessor_fwver_t v = {};
	if( esp_hosted_get_coprocessor_fwversion( &v ) != ESP_OK ) {
		ESP_LOGW( TAG, "The C6 didn't say which firmware it runs" );
		return;
	}
	snprintf( version, sizeof( version ), "%" PRIu32 ".%" PRIu32 ".%" PRIu32, v.major1, v.minor1, v.patch1 );
	ESP_LOGI( TAG, "C6 firmware %s (this bridge is built for ESP-Hosted %d.%d.%d)", version,
	          ESP_HOSTED_VERSION_MAJOR_1, ESP_HOSTED_VERSION_MINOR_1, ESP_HOSTED_VERSION_PATCH_1 );
#endif
}

void Coprocessor::addInfo( cJSON *network ) {
#if CONFIG_ESP_HOSTED_ENABLED
	if( version[0] )
		cJSON_AddStringToObject( network, "coprocessor", version );
	else
		cJSON_AddNullToObject( network, "coprocessor" );
#endif
}

void Coprocessor::registerHandler( httpd_handle_t server ) {
#if CONFIG_ESP_HOSTED_ENABLED
	httpd_uri_t update = { .uri = "/coprocessor", .method = HTTP_POST, .handler = updateHandler, .user_ctx = nullptr };
	httpd_register_uri_handler( server, &update );
#endif
}
