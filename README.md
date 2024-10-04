# hyproled

Hyprland shader utility to prevent OLED burn in. Disables every other pixel.

![Demo usage switching on and of](https://github.com/mklan/hyproled/blob/main/demo.gif)

## Usage

```
Usage: hyproled [OPTION]... [off]

Options:
  -a x:y:w:h  The effective area. Useful for bars. [Default: The entire screen]
  -i          Inverts the area. Useful for focus effect of a window or popup.
  -s          Swap lit pixels. Useful to prevent burn in, when called periodically.
  -m ID       Id of your oled monitor (hyprctl monitors). [Default: 0]
  -h          Display this help message.

Argument:
  off         Disables hyproled
```

## Install

1. Using AUR package [hyproled-git](https://aur.archlinux.org/packages/hyproled-git)

`yay -S hyproled-git`

or

2. `sudo cp hyproled /usr/bin/hyproled`

## Getting Started

Executing `hyproled` will overlay the entire screen with a 1px checkerboard.

Since the effect reduces the overall brightness, it might make sense to apply the shader only partially on highly static areas such as bars.

```bash
hyproled -s -a 0:0:2880:50
```

Each time this command is executed it swaps the disabled pixels. A great way to prevent burn in is to swap in an hourly cronjob.

```bash
 0 * * * *  hyproled -s -a 0:0:2880:50
```

Check the [examples](examples) for a more advanced burn in prevention script

## Disable in fullscreen apps

Especially when limiting hyproled to a specific area, it might make sense to disable the shader in fullscreen mode. 

```bash
#!/usr/bin/env bash

function handle {
  case $1 in
    'fullscreen>>1') hyproled off ;;
    'fullscreen>>0') hyproled -a 0:0:2880:50 ;;
 ;;
  esac
}

socat -U - UNIX-CONNECT:$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock | while read -r line; do handle "$line"; done
```

## Backlog

- Multiple areas
- Multiple monitors
- Combine with a passed shader, since hyprland allows only one shader
