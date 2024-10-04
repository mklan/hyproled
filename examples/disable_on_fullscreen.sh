#!/bin/bash

function handle {
  case $1 in
    'fullscreen>>1') hyproled off ;;
    'fullscreen>>0') hyproled -a 0:0:2880:45 ;;
 ;;
  esac
}

socat -U - UNIX-CONNECT:$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock | while read -r line; do handle "$line"; done