#!/usr/bin/env bash
set -euo pipefail
setup_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -x "$setup_root/.venv/bin/python" ]] || { echo '请先安装YAM Python环境'; exit 1; }
mkdir -p "$HOME/.config/systemd/user"
if [[ ! -f "$HOME/.config/systemd/user/yam-operator.service" ]]; then
cat > "$HOME/.config/systemd/user/yam-operator.service" <<UNIT
[Unit]
Description=YAM operator workbench
After=network.target
[Service]
WorkingDirectory=$setup_root
ExecStart=$setup_root/.venv/bin/python $setup_root/scripts/operator_web.py
Restart=on-failure
RestartSec=3
KillMode=process
[Install]
WantedBy=default.target
UNIT
fi
cat > "$HOME/.config/systemd/user/yam-station-setup.service" <<UNIT
[Unit]
Description=YAM local station setup wizard
After=network.target
[Service]
WorkingDirectory=$setup_root
ExecStart=/usr/bin/sg dialout -c "exec $setup_root/.venv/bin/python $setup_root/scripts/setup_web.py"
Restart=on-failure
RestartSec=3
[Install]
WantedBy=default.target
UNIT
mkdir -p "$HOME/.config/systemd/user/yam-operator.service.d"
cat > "$HOME/.config/systemd/user/yam-operator.service.d/serial-permissions.conf" <<UNIT
[Service]
Type=notify
NotifyAccess=all
ExecStart=
ExecStart=/usr/bin/sg dialout -c "exec $setup_root/.venv/bin/python $setup_root/scripts/operator_web.py"
UNIT
systemctl --user daemon-reload
systemctl --user enable --now yam-station-setup.service
printf '%s\n' '在本机浏览器打开 http://127.0.0.1:8772/ 配置工位。'
