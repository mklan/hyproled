
{
    description = "hyproled - Hyprland OLED mitigation utility";

    inputs = {
        nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    };

    outputs = {self, nixpkgs}: let
        systems = [
            "x86_64-linux"
            "aarch64-linux"
        ];

        forAllSystems = nixpkgs.lib.genAttrs systems;

        pkgsFor = system: import nixpkgs {
            inherit system;
        };
    in {

        packages = forAllSystems (system: let
            pkgs = pkgsFor system;

            runtimeInputs = with pkgs; [
                bash
                coreutils
                gawk
                jq
                util-linux
                brightnessctl
            ];

            hyproledPackage = pkgs.stdenvNoCC.mkDerivation {
                pname = "hyproled";
                version = "0.4.0-dev";

                src = self;
                dontUnpack = true;

                nativeBuildInputs = [
                    pkgs.makeWrapper
                ];

                installPhase = ''
                    runHook preInstall

                    install -Dm755 \
                        "$src/hyproled" \
                        "$out/bin/hyproled"

                    install -d \
                        "$out/share/hyproled/icons"

                    cp -r \
                        "$src/assets/icons/." \
                        "$out/share/hyproled/icons/"

                    install -Dm644 \
                        "$src/assets/waybar-icons.css" \
                        "$out/share/hyproled/waybar-icons.css"

                    wrapProgram "$out/bin/hyproled" \
                        --prefix PATH : \
                        "${pkgs.lib.makeBinPath runtimeInputs}"

                    runHook postInstall
                '';

                meta = {
                    description = "Hyprland shader utility for OLED burn-in mitigation";
                    mainProgram = "hyproled";
                    platforms = pkgs.lib.platforms.linux;
                };
            };
        in {
            default = hyproledPackage;
            hyproled = hyproledPackage;
        });

        devShells = forAllSystems (system: let
            pkgs = pkgsFor system;
        in {
            default = pkgs.mkShell {
                packages = with pkgs; [
                    bash
                    coreutils
                    gawk
                    jq
                    util-linux
                    brightnessctl
                    python3
                    glslang
                    alejandra
                ];
            };
        });

        checks = forAllSystems (system: let
            pkgs = pkgsFor system;
        in {
            tests = pkgs.runCommand "hyproled-tests" {
                nativeBuildInputs = with pkgs; [
                    bash
                    coreutils
                    gawk
                    jq
                    util-linux
                    python3
                    glslang
                ];
            } ''
                export HOME="$TMPDIR/home"
                export PYTHONDONTWRITEBYTECODE=1

                source="$TMPDIR/source"

                mkdir -p "$HOME" "$source"

                cp -r ${self}/. "$source/"
                chmod -R u+w "$source"

                patchShebangs "$source"

                cd "$source"

                python3 -m unittest discover -s tests -v

                touch "$out"
            '';
        });

        formatter = forAllSystems (system:
            (pkgsFor system).alejandra
        );

        nixosModules.default = {
            config,
            lib,
            pkgs,
            ...
        }: let
            cfg = config.programs.hyproled;
        in {
            options.programs.hyproled = {
                enable = lib.mkEnableOption "hyproled";

                package = lib.mkOption {
                    type = lib.types.package;
                    default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
                    description = "The hyproled package to install";
                };
            };

            config = lib.mkIf cfg.enable {
                environment.systemPackages = [
                    cfg.package
                ];
            };
        };
    };
}