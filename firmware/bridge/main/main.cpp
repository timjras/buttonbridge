// ButtonBridge bridge firmware: an ESP32-S3 or ESP32-P4 between the network and a Stream
// Deck on its USB host port (the S3's native USB port, or the P4's high-speed USB-A port).
// The renderer (on the Mac, later Home Assistant) finds it over mDNS, sends key images over
// a WebSocket and gets button presses back. See docs/BRIDGE-PROTOCOL.md.
#include <cstdio>
#include <cstring>

#include "Bridge.h"
#include "Config.h"
#include "LogBuffer.h"
#include "Network.h"
#include "Provisioning.h"
#include "StreamDeck.h"
#include "driver/usb_serial_jtag.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

namespace {
	constexpr const char *TAG = "main";

	StreamDeck deck;
	char       hostname[32];
	char       macText[18];
	bool       computerOnUSB = false;

#if CONFIG_IDF_TARGET_ESP32S3
	// A computer on the native USB port sends start-of-frame packets every millisecond, which
	// the USB-Serial-JTAG peripheral notices. With the OTG adapter and a Stream Deck there's no
	// host upstream and so no SOFs. (The same check as ESPDeck's.)
	bool detectComputer() {
		constexpr uint32_t kSettle  = 20;     // ms; the SOF monitor starts out assuming "connected"
		constexpr uint32_t kTimeout = 1500;
		vTaskDelay( pdMS_TO_TICKS( kSettle ) );
		uint32_t waited = kSettle;
		while( !usb_serial_jtag_is_connected() && waited < kTimeout ) {
			vTaskDelay( pdMS_TO_TICKS( 10 ) );
			waited += 10;
		}
		bool connected = usb_serial_jtag_is_connected();
		if( connected )
			ESP_LOGI( TAG, "USB port: computer; staying a serial port, no Stream Deck" );
		else
			ESP_LOGI( TAG, "USB port: no computer; starting the USB host for a Stream Deck" );
		return connected;
	}
#endif

	void networkUp() {
		// A new image from an update over the network has proved it can get back on the network.
		esp_ota_mark_app_valid_cancel_rollback();
		Bridge::networkUp();
	}
}

extern "C" void app_main( void ) {
	LogBuffer::begin();
	ESP_LOGI( TAG, "ButtonBridge bridge %s", firmwareVersion() );

	esp_err_t err = nvs_flash_init();
	if( err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND ) {
		ESP_ERROR_CHECK( nvs_flash_erase() );
		err = nvs_flash_init();
	}
	ESP_ERROR_CHECK( err );
	ESP_ERROR_CHECK( esp_netif_init() );
	ESP_ERROR_CHECK( esp_event_loop_create_default() );

	uint8_t mac[6];
	esp_read_mac( mac, ESP_MAC_BASE );   // the Wi-Fi station MAC on the S3
	snprintf( macText, sizeof( macText ), "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5] );
	snprintf( hostname, sizeof( hostname ), "sdbridge-%02x%02x%02x", mac[3], mac[4], mac[5] );

#if CONFIG_IDF_TARGET_ESP32S3
	// The USB host stack takes the port over from USB-Serial-JTAG, so only with no computer.
	computerOnUSB = detectComputer();
#endif
	Provisioning::begin( computerOnUSB, Bridge::infoJSON );
	bool usbHost = !computerOnUSB && deck.begin();
	if( !computerOnUSB && !usbHost )
		ESP_LOGE( TAG, "USB host didn't start" );

	Bridge::begin( deck, usbHost, hostname, macText );
	Network::begin( hostname, networkUp );

	StreamDeck::Event event;
	for( ;; ) {
		if( !computerOnUSB && deck.nextEvent( event, portMAX_DELAY ) )
			Bridge::handleDeckEvent( event );
		else if( computerOnUSB )
			vTaskDelay( portMAX_DELAY );
	}
}
