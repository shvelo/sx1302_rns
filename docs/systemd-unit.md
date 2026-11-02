# systemd unit for Reticulum daemon with SX1302 interface

This is an optional lightweight unit for running `rnsd` as a system service.
It is **not** a radio daemon — it just manages the Reticulum network stack.

## 1. Install the unit

```bash
# As root:
cp docs/rnsd-sx1302.service /etc/systemd/system/
systemctl daemon-reload
```

## 2. Configure

Create `/etc/reticulum/config` (or `~/.reticulum/config` for user service)
with your `[interfaces]` stanza. The unit runs as user `pi` by default.

```ini
# /etc/reticulum/config
[reticulum]
  enable_transport = no
  share_instance = yes
  instance_name = default

[logging]
  loglevel = 3

[interfaces]

  [[SX1302 LoRa]]
    type = SX1302
    enabled = yes
    board_profile = /home/pi/sx1302_rns/examples/rak2287-eu868.conf
    # library = /home/pi/sx1302_rns/build/libloragw.so
```

Adjust paths and user in the unit file if needed.

## 3. Enable and start

```bash
# System-wide (as root):
systemctl enable --now rnsd-sx1302

# Or user service (as pi):
systemctl --user enable --now rnsd-sx1302
```

## 4. Unit file contents

```ini
# /etc/systemd/system/rnsd-sx1302.service
[Unit]
Description=Reticulum Network Stack (SX1302 interface)
Documentation=https://reticulum.network/
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
Group=pi
SupplementaryGroups=spi gpio i2c
WorkingDirectory=/home/pi/sx1302_rns
ExecStartPre=/home/pi/sx1302_rns/scripts/reset_rak2287.sh start
ExecStart=/home/pi/sx1302_rns/.venv/bin/rnsd --config /etc/reticulum/config
ExecStopPost=/home/pi/sx1302_rns/scripts/reset_rak2287.sh stop
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=rnsd-sx1302

# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/pi/sx1302_rns /etc/reticulum /var/log/reticulum
DeviceAllow=/dev/spidev0.0 rw
DeviceAllow=/dev/gpiochip* rw
DeviceAllow=/dev/i2c-1 rw

# Environment
Environment=SX1302_HAL_LIB=/home/pi/sx1302_rns/build/libloragw.so
Environment=PYTHONPATH=/home/pi/sx1302_rns
Environment=PATH=/home/pi/sx1302_rns/.venv/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

## 5. Notes

- The `ExecStartPre`/`ExecStopPost` ensure the SX1302 reset line is driven
  correctly even if `rnsd` crashes. The HAL also runs its own reset in the
  worker's `stop()` method, so this is a belt-and-suspenders approach.
- `SupplementaryGroups=spi gpio i2c` gives the service access to the
  required device nodes. On Trixie, these groups exist by default.
- `DeviceAllow` restricts device access to only what's needed.
- The virtual environment's Python and `rnsd` are used via the `PATH`
  and `ExecStart` absolute path.

## 6. User service alternative

For a user service (no root required), create
`~/.config/systemd/user/rnsd-sx1302.service` with the same content but
without `User=`, `Group=`, and with absolute paths to your home directory.
Then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now rnsd-sx1302
loginctl enable-linger pi  # keep user service running after logout
```