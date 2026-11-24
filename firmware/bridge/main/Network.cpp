#include "Network.h"

#include <cstring>

#include "Config.h"
#include "Coprocessor.h"
#include "Provisioning.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include <atomic>

#if CONFIG_BRIDGE_ETHERNET
#include "esp_eth.h"
#include "esp_eth_mac_esp.h"
#endif
#if CONFIG_BRIDGE_WIFI
#include "esp_wifi.h"
#endif

namespace {
	constexpr const char *TAG = "network";

	const char           *hostname = nullptr;
	Network::IPCallback   onIP     = nullptr;
	char                  use[10]  = "auto";
	std::atomic<const char *> active { nullptr };   // "ethernet" or "wifi" while it has an address

	void gotIP( void *, esp_event_base_t, int32_t id, void *data ) {
		auto *event = (ip_event_got_ip_t *)data;
		active      = id == IP_EVENT_ETH_GOT_IP ? "ethernet" : "wifi";
		ESP_LOGI( TAG, "On the network (%s) as " IPSTR " (%s.local)", active.load(),
		          IP2STR( &event->ip_info.ip ), hostname );
		if( onIP )
			onIP();
	}

	void lostIP( void *, esp_event_base_t, int32_t, void * ) {
		active = nullptr;
	}

#if CONFIG_BRIDGE_ETHERNET
	std::atomic<bool> ethernetLink { false };

	void ethernetEvent( void *, esp_event_base_t, int32_t id, void * ) {
		if( id == ETHERNET_EVENT_CONNECTED ) {
			ethernetLink = true;
			ESP_LOGI( TAG, "Ethernet link up" );
		} else if( id == ETHERNET_EVENT_DISCONNECTED ) {
			ethernetLink = false;
			if( active.load() && strcmp( active.load(), "ethernet" ) == 0 )
				active = nullptr;
			ESP_LOGW( TAG, "Ethernet link down" );
		}
	}

	// The ESP32-P4's own Ethernet MAC with the board's IP101 PHY. The SMI and RMII clock pins
	// are ESP-IDF's defaults for the P4, which the Waveshare ESP32-P4-WIFI6-POE-ETH follows.
	bool startEthernet() {
		esp_netif_config_t netifConfig = ESP_NETIF_DEFAULT_ETH();
		esp_netif_t       *netif       = esp_netif_new( &netifConfig );
		esp_netif_set_hostname( netif, hostname );

		eth_mac_config_t           macConfig  = ETH_MAC_DEFAULT_CONFIG();
		eth_esp32_emac_config_t    emacConfig = ETH_ESP32_EMAC_DEFAULT_CONFIG();
		emacConfig.smi_gpio.mdc_num  = CONFIG_BRIDGE_ETH_MDC_GPIO;
		emacConfig.smi_gpio.mdio_num = CONFIG_BRIDGE_ETH_MDIO_GPIO;
		eth_phy_config_t phyConfig   = ETH_PHY_DEFAULT_CONFIG();
		phyConfig.phy_addr           = CONFIG_BRIDGE_ETH_PHY_ADDR;
		phyConfig.reset_gpio_num     = CONFIG_BRIDGE_ETH_PHY_RST_GPIO;

		esp_eth_mac_t *mac = esp_eth_mac_new_esp32( &emacConfig, &macConfig );
		esp_eth_phy_t *phy = esp_eth_phy_new_ip101( &phyConfig );
		esp_eth_handle_t   handle = nullptr;
		esp_eth_config_t   config = ETH_DEFAULT_CONFIG( mac, phy );
		if( !mac || !phy || esp_eth_driver_install( &config, &handle ) != ESP_OK ) {
			ESP_LOGE( TAG, "Ethernet didn't start" );
			return false;
		}
		esp_netif_attach( netif, esp_eth_new_netif_glue( handle ) );
		esp_event_handler_register( ETH_EVENT, ESP_EVENT_ANY_ID, ethernetEvent, nullptr );
		esp_eth_start( handle );
		ESP_LOGI( TAG, "Ethernet started" );
		return true;
	}
#endif

#if CONFIG_BRIDGE_WIFI
	char ssid[33] = {}, password[65] = {};
	bool wifiSaved   = false;
	bool wifiStarted = false;

	void wifiEvent( void *, esp_event_base_t, int32_t id, void *data ) {
		if( id == WIFI_EVENT_STA_START ) {
			esp_wifi_connect();
		} else if( id == WIFI_EVENT_STA_DISCONNECTED ) {
			auto *event = (wifi_event_sta_disconnected_t *)data;
			active      = nullptr;
			ESP_LOGW( TAG, "Wi-Fi disconnected (reason %u); retrying", event->reason );
			vTaskDelay( pdMS_TO_TICKS( kWifiRetryMs ) );
			esp_wifi_connect();
		}
	}

	// On the ESP32-P4 the esp_wifi calls below go to the ESP32-C6 over SDIO (ESP-Hosted).
	bool startWifi() {
		if( !wifiSaved ) {
			ESP_LOGW( TAG, "No Wi-Fi network set. Run tools/set_wifi.py with the board plugged into the Mac." );
			return false;
		}

		esp_netif_t *netif = esp_netif_create_default_wifi_sta();
		esp_netif_set_hostname( netif, hostname );
		wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
		ESP_ERROR_CHECK( esp_wifi_init( &init ) );
		Coprocessor::readVersion();
		esp_event_handler_register( WIFI_EVENT, ESP_EVENT_ANY_ID, wifiEvent, nullptr );

		wifi_config_t config = {};
		strlcpy( (char *)config.sta.ssid, ssid, sizeof( config.sta.ssid ) );
		strlcpy( (char *)config.sta.password, password, sizeof( config.sta.password ) );
		config.sta.scan_method        = WIFI_ALL_CHANNEL_SCAN;
		config.sta.sort_method        = WIFI_CONNECT_AP_BY_SIGNAL;
		config.sta.threshold.authmode = password[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
		ESP_ERROR_CHECK( esp_wifi_set_mode( WIFI_MODE_STA ) );
		ESP_ERROR_CHECK( esp_wifi_set_config( WIFI_IF_STA, &config ) );
		ESP_ERROR_CHECK( esp_wifi_start() );
		esp_wifi_set_ps( WIFI_PS_NONE );   // key presses go out without waiting for a beacon
		wifiStarted = true;
		ESP_LOGI( TAG, "Joining Wi-Fi network \"%s\"", ssid );
		return true;
	}
#endif

#if CONFIG_BRIDGE_ETHERNET && CONFIG_BRIDGE_WIFI
	// "auto": give the cable a few seconds to get a link, then use Wi-Fi instead.
	void wifiIfNoCable( void * ) {
		vTaskDelay( pdMS_TO_TICKS( kEthernetLinkWaitMs ) );
		if( !ethernetLink ) {
			ESP_LOGI( TAG, "No Ethernet link after %u s; using Wi-Fi", (unsigned)( kEthernetLinkWaitMs / 1000 ) );
			startWifi();
		}
		vTaskDelete( nullptr );
	}
#endif
}

bool Network::begin( const char *theHostname, IPCallback theOnIP ) {
	hostname = theHostname;
	onIP     = theOnIP;
	esp_event_handler_register( IP_EVENT, IP_EVENT_STA_GOT_IP, gotIP, nullptr );
	esp_event_handler_register( IP_EVENT, IP_EVENT_ETH_GOT_IP, gotIP, nullptr );
	esp_event_handler_register( IP_EVENT, IP_EVENT_STA_LOST_IP, lostIP, nullptr );
	esp_event_handler_register( IP_EVENT, IP_EVENT_ETH_LOST_IP, lostIP, nullptr );
#if CONFIG_BRIDGE_WIFI
	wifiSaved = Provisioning::loadWifi( ssid, password );
#endif

#if CONFIG_BRIDGE_ETHERNET && CONFIG_BRIDGE_WIFI
	Provisioning::loadNetworkUse( use );
	if( strcmp( use, "wifi" ) == 0 ) {
		if( wifiSaved )
			return startWifi();
		ESP_LOGW( TAG, "Set to use Wi-Fi but no network is saved; using Ethernet" );
		return startEthernet();
	}
	if( strcmp( use, "ethernet" ) == 0 || !wifiSaved )
		return startEthernet();
	// auto, with a Wi-Fi network saved
	if( !startEthernet() )
		return startWifi();
	xTaskCreate( wifiIfNoCable, "wifi-fallback", 4096, nullptr, 3, nullptr );
	return true;
#elif CONFIG_BRIDGE_ETHERNET
	strlcpy( use, "ethernet", sizeof( use ) );
	return startEthernet();
#elif CONFIG_BRIDGE_WIFI
	strlcpy( use, "wifi", sizeof( use ) );
	return startWifi();
#else
	return false;
#endif
}

void Network::addInfo( cJSON *parent ) {
	cJSON *network = cJSON_AddObjectToObject( parent, "network" );
	cJSON *has     = cJSON_AddArrayToObject( network, "has" );
#if CONFIG_BRIDGE_ETHERNET
	cJSON_AddItemToArray( has, cJSON_CreateString( "ethernet" ) );
#endif
#if CONFIG_BRIDGE_WIFI
	cJSON_AddItemToArray( has, cJSON_CreateString( "wifi" ) );
#endif
	cJSON_AddStringToObject( network, "use", use );
	Coprocessor::addInfo( network );
	const char *now = active;
	if( now )
		cJSON_AddStringToObject( network, "active", now );
	else
		cJSON_AddNullToObject( network, "active" );
#if CONFIG_BRIDGE_WIFI
	if( wifiSaved )
		cJSON_AddStringToObject( network, "ssid", ssid );
	wifi_ap_record_t ap;
	if( wifiStarted && now && strcmp( now, "wifi" ) == 0 && esp_wifi_sta_get_ap_info( &ap ) == ESP_OK )
		cJSON_AddNumberToObject( network, "rssi", ap.rssi );
#endif
}
