#!/bin/bash

notify-send "Shifting pixels..."
sleep 1

# Render overlay over bar
hyproled -s -a 0:0:2880:48

in=3
out=3

if [ "$(hyprshade current)" = "hyproled_shader" ]; then
    in=1
    out=1
fi

hyprctl eval "hl.config({ general = { gaps_in = $in, gaps_out = $out } })"
