# Security

Bridges accept commands from anything on the local network without a password: key images, a
firmware update (`POST /update`), and the Wi-Fi settings on the ESP32-S3. Keep them on a network you
trust, or on a separate VLAN that only Home Assistant can reach.

The Home Assistant app talks to Home Assistant through the Supervisor's API and needs no token.

To report a security problem, please open a private security advisory on GitHub
(**Security > Report a vulnerability**) rather than a public issue.
