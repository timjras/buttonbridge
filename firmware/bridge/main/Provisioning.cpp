#include "Provisioning.h"

#include <cstdio>
#include <cstring>

#include "cJSON.h"
#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"

namespace {
	constexpr const char *TAG          = "provision";
	constexpr const char *kNamespace   = "wifi";
	constexpr size_t      kLineMax     = 512;
	constexpr size_t      kRxBuffer    = 1024;
	constexpr uint32_t    kRestartWait = 500;   // ms for the reply to go out

	bool                       usbSerial = false;
	Provisioning::InfoFunction infoFunction = nullptr;

	struct Line {
		char   text[kLineMax];
		size_t length = 0;
		bool   overflow = false;
	};

	void reply( const char *json ) {
		printf( "@@%s\n", json );
		fflush( stdout );
	}

	void replyError( const char *message ) {
		char out[160];
		snprintf( out, sizeof( out ), "{\"ok\":false,\"error\":\"%s\"}", message );
		reply( out );
	}

	bool saveWifi( const char *ssid, const char *password ) {
		nvs_handle_t nvs;
		if( nvs_open( kNamespace, NVS_READWRITE, &nvs ) != ESP_OK )
			return false;
		bool ok = nvs_set_str( nvs, "ssid", ssid ) == ESP_OK &&
		          nvs_set_str( nvs, "password", password ) == ESP_OK &&
		          nvs_commit( nvs ) == ESP_OK;
		nvs_close( nvs );
		return ok;
	}

	bool saveNetworkUse( const char *use ) {
		nvs_handle_t nvs;
		if( nvs_open( kNamespace, NVS_READWRITE, &nvs ) != ESP_OK )
			return false;
		bool ok = nvs_set_str( nvs, "use", use ) == ESP_OK && nvs_commit( nvs ) == ESP_OK;
		nvs_close( nvs );
		return ok;
	}

	void restart() {
		vTaskDelay( pdMS_TO_TICKS( kRestartWait ) );
		esp_restart();
	}

	void handleLine( const char *text ) {
		cJSON *json = cJSON_Parse( text );
		if( !json )
			return;   // not for us (a terminal's keystrokes, say)
		const cJSON *cmd = cJSON_GetObjectItem( json, "cmd" );
		if( !cJSON_IsString( cmd ) ) {
			cJSON_Delete( json );
			return;
		}

		if( strcmp( cmd->valuestring, "wifi" ) == 0 ) {
			const cJSON *ssid     = cJSON_GetObjectItem( json, "ssid" );
			const cJSON *password = cJSON_GetObjectItem( json, "password" );
			if( !cJSON_IsString( ssid ) || !cJSON_IsString( password ) ||
			    strlen( ssid->valuestring ) < 1 || strlen( ssid->valuestring ) > 32 ||
			    strlen( password->valuestring ) > 64 ) {
				replyError( "ssid must be 1-32 characters and password at most 64" );
			} else if( !saveWifi( ssid->valuestring, password->valuestring ) ) {
				replyError( "couldn't save to NVS" );
			} else {
				ESP_LOGI( TAG, "Saved Wi-Fi network \"%s\"; restarting", ssid->valuestring );
				reply( "{\"ok\":true,\"restarting\":true}" );
				restart();
			}
		} else if( strcmp( cmd->valuestring, "network" ) == 0 ) {
			const cJSON *use = cJSON_GetObjectItem( json, "use" );
			if( !cJSON_IsString( use ) || ( strcmp( use->valuestring, "auto" ) != 0 &&
			                                 strcmp( use->valuestring, "ethernet" ) != 0 &&
			                                 strcmp( use->valuestring, "wifi" ) != 0 ) ) {
				replyError( "use must be auto, ethernet or wifi" );
			} else if( !saveNetworkUse( use->valuestring ) ) {
				replyError( "couldn't save to NVS" );
			} else {
				ESP_LOGI( TAG, "Network set to %s; restarting", use->valuestring );
				reply( "{\"ok\":true,\"restarting\":true}" );
				restart();
			}
		} else if( strcmp( cmd->valuestring, "info" ) == 0 && infoFunction ) {
			static char out[12 * 1024];   // room for every cached image's hash
			infoFunction( out, sizeof( out ) );
			reply( out );
		} else {
			replyError( "unknown cmd" );
		}
		cJSON_Delete( json );
	}

	void receive( Line &line, uint8_t value ) {
		if( value == '\n' || value == '\r' ) {
			if( line.length > 0 && !line.overflow ) {
				line.text[line.length] = 0;
				handleLine( line.text );
			}
			line.length   = 0;
			line.overflow = false;
		} else if( line.length < kLineMax - 1 ) {
			line.text[line.length++] = (char)value;
		} else {
			line.overflow = true;
		}
	}

	void task( void * ) {
		static Line uartLine, usbLine;
		uint8_t     bytes[64];
		for( ;; ) {
			int count;
			while( ( count = uart_read_bytes( UART_NUM_0, bytes, sizeof( bytes ), 0 ) ) > 0 )
				for( int i = 0; i < count; i++ )
					receive( uartLine, bytes[i] );
			while( usbSerial && ( count = usb_serial_jtag_read_bytes( bytes, sizeof( bytes ), 0 ) ) > 0 )
				for( int i = 0; i < count; i++ )
					receive( usbLine, bytes[i] );
			vTaskDelay( pdMS_TO_TICKS( 20 ) );
		}
	}
}

void Provisioning::begin( bool usb, InfoFunction info ) {
	infoFunction = info;

	// Receive-only driver on the console UART; logs keep going straight to its FIFO.
	if( !uart_is_driver_installed( UART_NUM_0 ) )
		uart_driver_install( UART_NUM_0, kRxBuffer, 0, 0, nullptr, 0 );

	// On the native USB port the logs go through the driver too, so both share its buffer.
	if( usb && !usb_serial_jtag_is_driver_installed() ) {
		usb_serial_jtag_driver_config_t config = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
		config.rx_buffer_size = kRxBuffer;
		config.tx_buffer_size = kRxBuffer;
		if( usb_serial_jtag_driver_install( &config ) == ESP_OK ) {
			usb_serial_jtag_vfs_use_driver();
			usbSerial = true;
		}
	}

	xTaskCreate( task, "provision", 6144, nullptr, 2, nullptr );
	ESP_LOGI( TAG, "Listening for setup commands on UART0%s", usbSerial ? " and USB" : "" );
}

bool Provisioning::loadWifi( char ( &ssid )[33], char ( &password )[65] ) {
	nvs_handle_t nvs;
	if( nvs_open( kNamespace, NVS_READONLY, &nvs ) != ESP_OK )
		return false;
	size_t ssidLength = sizeof( ssid ), passwordLength = sizeof( password );
	bool   ok = nvs_get_str( nvs, "ssid", ssid, &ssidLength ) == ESP_OK &&
	            nvs_get_str( nvs, "password", password, &passwordLength ) == ESP_OK;
	nvs_close( nvs );
	return ok && ssid[0];
}

void Provisioning::loadNetworkUse( char ( &use )[10] ) {
	nvs_handle_t nvs;
	if( nvs_open( kNamespace, NVS_READONLY, &nvs ) != ESP_OK )
		return;
	char   saved[10];
	size_t length = sizeof( saved );
	if( nvs_get_str( nvs, "use", saved, &length ) == ESP_OK )
		strlcpy( use, saved, sizeof( use ) );
	nvs_close( nvs );
}
