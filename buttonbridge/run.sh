#!/usr/bin/with-contenv bashio
# Starts the renderer with the add-on's options. Home Assistant's config folder is at
# /homeassistant, and the Supervisor's API proxy stands in for Home Assistant's address.
CONFIG="/homeassistant/$(bashio::config 'config_file')"
ARGS=()
if bashio::config.true 'debug'; then
    ARGS+=(--verbose)
fi
if [ ! -f "${CONFIG}" ]; then
    bashio::log.warning "${CONFIG} doesn't exist yet: every panel will show its ID until you create it."
fi
cd /app
exec python3 -m renderer run "${CONFIG}" --ha http://supervisor/core --web-port 8099 --ingress "${ARGS[@]}"
