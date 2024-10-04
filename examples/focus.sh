#!/bin/bash

# Focus current active window by masking everything around

scalefactor=2

area=$(hyprctl activewindow -j | jq -r --arg sf "$scalefactor" '
  .at as $at |
  .size as $size |
  ($at[0] * ($sf|tonumber)) as $x |
  ($at[1] * ($sf|tonumber)) as $y |
  ($size[0] * ($sf|tonumber)) as $w |
  ($size[1] * ($sf|tonumber)) as $h |
  "\($x):\($y):\($w):\($h)"
')

hyproled -i -a $area