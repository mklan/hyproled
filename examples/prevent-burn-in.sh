#!/bin/bash

# Call me every now and then using cron

# Render overlay over bar (swap each call)
hyproled -s -a 0:0:2880:46

# More precoutions in addition to hyproled

# Change gaps to prevent the borders burning in
in=3;
out=6;
if [ "$(hyprshade current)" == "hyproled_shader" ]; then
    in=2;
    out=4;
fi

hyprctl --instance 0 keyword general:gaps_in $in;
hyprctl --instance 0 keyword general:gaps_out $out;