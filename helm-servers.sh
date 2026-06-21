#!/bin/bash
# helm-servers.sh -- DEPRECATED. Prefer:  helm restart
#
# The HELM server is launchd-managed (com.helm.server, KeepAlive): it starts on
# login and respawns on its own. This script no longer launches a server -- it just
# restarts the managed agent (kill + relaunch, re-reading code). Only touches
# com.helm.server; never COTS (cots.local:8765).

LABEL="com.helm.server"
echo "[warn] helm-servers.sh is deprecated -- use 'helm restart'."
echo "       Restarting launchd agent ${LABEL} ..."
if launchctl kickstart -k "gui/$(id -u)/${LABEL}"; then
  echo "[ok] HELM server restarted on http://helm.local:8766"
else
  echo "[fail] kickstart failed -- is ${LABEL} loaded?  launchctl list | grep helm"
  exit 1
fi
