// Derived from ESPDeck (https://github.com/jangellx/ESPDeck), Copyright (c) 2026 Joe Angell,
// MIT License. See THIRD-PARTY-NOTICES.md.
//
// Stream Decks over the ESP-IDF USB Host library and HID host class driver.
//
// Every model with keys is supported, from a table keyed by USB product ID (layouts and
// report formats from python-elgato-streamdeck, which is tested on hardware). There are
// three report families:
//   Mini      Mini, Mini 2022, Mini Discord, 6-Key Module: BMP images in 1024-byte reports
//   Original  the first 15-key Stream Deck: BMP images in 8191-byte reports
//   Main      everything since (MK.2, XL, Neo, Plus, Pedal, modules): JPEG images
// Besides the keys, the +'s four dials and touch strip are read and its strip can be drawn
// on; the Neo's info screen can be drawn on too, but its touch keys are ignored.
// Protocol: https://docs.elgato.com/streamdeck/hid/
//
// The model layouts and USB report formats are derived from python-elgato-streamdeck,
// Copyright (c) Dean Camera, MIT License (https://github.com/abcminiuser/python-elgato-streamdeck).
// See THIRD-PARTY-NOTICES.md for the full license text.
//
// Key indices here are row-major from the top-left as seen from the front, whatever the
// model's own wire order.
#pragma once

#include <cstddef>
#include <cstdint>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "usb/hid_host.h"
#include "usb/usb_host.h"

#include "Config.h"

class StreamDeck {
public:
	// What nextEvent() reports.
	enum class EventType : uint8_t {
		Connected,
		Disconnected,
		KeyDown,
		KeyUp,
		DialTurn,    // a dial turned `delta` detents (positive is clockwise)
		DialDown,    // a dial pushed in
		DialUp,
		Touch,       // the touch strip; see touch
		UsbDevice,   // any USB device was plugged in; see lastUsbDevice()
	};

	// What a finger did on the touch strip.
	enum class TouchKind : uint8_t {
		Tap   = 1,
		Long  = 2,   // a long press
		Swipe = 3,   // from (x, y) to (toX, toY)
	};

	// What was last plugged into the USB port, Stream Deck or not. vid is 0 if it couldn't be read.
	struct UsbDevice {
		bool     seen;          // false until something has been plugged in
		uint16_t vid;
		uint16_t pid;
		uint8_t  deviceClass;   // 0x09 is a hub; 0x00 means per interface (HID for a deck)
	};

	struct Event {
		EventType type;
		uint8_t   key;     // KeyDown and KeyUp; the dial for the dial events, from 0 at the left
		int8_t    delta;   // DialTurn
		TouchKind touch;   // Touch, in pixels on the strip from its top left
		uint16_t  x, y, toX, toY;
	};

	// The report family (see above).
	enum class Protocol : uint8_t {
		Mini,
		Original,
		Main,
	};

	// The key images' format.
	enum class Format : uint8_t {
		None,       // no displays (Pedal)
		BMP,
		JPEG,
	};

	// See PROTOCOL.md for how each maps pixels.
	enum class Transform : uint8_t {
		None,
		Transpose,
		Rotate90,
		Rotate270,
		Rotate180,
	};

	// The connected deck.
	struct Info {
		uint16_t  pid;
		char      model[40];
		Protocol  protocol;
		uint8_t   rows;
		uint8_t   cols;
		uint16_t  keySize;     // pixels, square
		Format    format;
		Transform transform;   // the model's default
		bool      reversed;    // wire key IDs run right to left within each row
		char      serial[33];
		char      firmware[33];
		// A screen besides the keys (the Neo's info bar, the +'s touch strip); 0 × 0 if none.
		uint16_t  screenWidth;
		uint16_t  screenHeight;
		Transform screenTransform;
		uint8_t   dials;       // the +'s knobs; 0 on other models

		uint8_t keyCount() const { return rows * cols; }
	};

	// Names as the protocol uses them ("jpeg", "rotate180", …).
	static const char *formatName( Format format );
	static const char *transformName( Transform transform );
	static bool        transformFromName( const char *name, Transform &out );

	// Installs the USB host stack and HID driver on the native USB port.
	bool begin();

	// Connection changes and key presses, in order.
	bool nextEvent( Event &event, TickType_t wait = 0 );

	// Whether a deck is connected, and the details of the last one to connect.
	bool isConnected() const;
	Info info() const;
	UsbDevice lastUsbDevice() const;

	// Uploads a ready-to-display image (keySize square, in the model's format, transform
	// already applied) to a key.
	esp_err_t setKeyImage( uint8_t key, const uint8_t *image, size_t length );

	// Uploads a JPEG (width × height, screenTransform applied) to the extra screen at (x, y).
	// The +'s strip takes any part of itself; the Neo's info bar only the whole of it.
	esp_err_t setScreenImage( uint16_t x, uint16_t y, uint16_t width, uint16_t height, const uint8_t *image, size_t length );

	// 0–100.
	esp_err_t setBrightness( uint8_t percent );

private:
	// Work for deviceTask(), from the HID driver's callbacks.
	enum class RequestType : uint8_t {
		Connected,
		Disconnected,
	};

	struct Request {
		RequestType              type;
		hid_host_device_handle_t handle;
	};

	// Tasks and callbacks (see StreamDeck.cpp).
	static void usbLibraryTask( void *arg );
	static void clientTask( void *arg );
	static void deviceTask( void *arg );
	static void driverCallback( hid_host_device_handle_t handle, const hid_host_driver_event_t event, void *arg );
	static void interfaceCallback( hid_host_device_handle_t handle, const hid_host_interface_event_t event, void *arg );
	static void clientEventCallback( const usb_host_client_event_msg_t *message, void *arg );
	static void transferDone( usb_transfer_t *transfer );

	void handleConnected( hid_host_device_handle_t handle );
	void handleDisconnected( hid_host_device_handle_t handle );
	void handleInputReport( const uint8_t *data, size_t length );
	void handleDialReport( const uint8_t *data, size_t length );
	void handleTouchReport( const uint8_t *data, size_t length );
	// Fills in info for an Elgato device with keys; false for anything else.
	bool identify( hid_host_device_handle_t handle, uint16_t vid, uint16_t pid, Info &info );
	void readFeatureString( hid_host_device_handle_t handle, uint8_t reportID, size_t length, size_t offset, char *out, size_t outSize );
	bool openOutput( hid_host_device_handle_t handle );
	// Shrinks an interrupt OUT endpoint the host can't take, before the interface is claimed.
	void shrinkOversizedOut( hid_host_device_handle_t handle );
	void closeOutput();
	esp_err_t sendReport( size_t length );
	template <typename FillHeader>
	esp_err_t sendImage( const uint8_t *image, size_t length, size_t header, FillHeader fillHeader, size_t &sent );
	// Clears a half-sent image and turns the deck's own sleep timer off.
	void prepareDeck( hid_host_device_handle_t handle );
	esp_err_t submitAndWait( bool control );
	// Maps a key index to the deck's wire order and back.
	uint8_t wireKey( uint8_t key ) const;
	void post( EventType type, uint8_t key = 0 );
	void post( const Event &event );
	void recordUsbDevice( const UsbDevice &device );

	QueueHandle_t            events_       = nullptr;
	QueueHandle_t            requests_     = nullptr;
	SemaphoreHandle_t        mutex_        = nullptr;   // guards handle_, info_, usbDevice_, report_ and the output transfer

	hid_host_device_handle_t handle_       = nullptr;
	Info                     info_         = {};
	UsbDevice                usbDevice_    = {};
	uint8_t                  keyStates_[kMaxKeys] = {};
	uint8_t                  dialStates_[kMaxDials] = {};
	uint8_t                 *report_       = nullptr;   // one output report, built before sending

	// Output reports go out on our own USB host client: the HID driver's control transfer
	// buffer is too small for image reports. They use the interrupt OUT endpoint when the
	// deck has one, and SET_REPORT on the control pipe otherwise.
	usb_host_client_handle_t client_       = nullptr;
	usb_device_handle_t      device_       = nullptr;
	usb_transfer_t          *transfer_     = nullptr;
	SemaphoreHandle_t        transferSem_  = nullptr;
	uint8_t                  interface_    = 0;
	uint8_t                  outEndpoint_  = 0;         // 0 if the interface has none
	uint16_t                 outPacket_    = 0;         // its max packet size
	bool                     useInterrupt_ = false;
	bool                     outShrunk_    = false;     // shrinkOversizedOut() cut its packets to 64 bytes
	bool                     stuck_        = false;     // a transfer never completed; wait for unplug
};
