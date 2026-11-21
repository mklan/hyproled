
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
                version = pkgs.lib.removeSuffix "\n" (builtins.readFile ./version.txt);

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

                    install -Dm644 \
                        "$src/version.txt" \
                        "$out/share/hyproled/version.txt"

                    install -d \
                        "$out/share/hyproled/icons"

                    cp -r \
                        "$src/assets/icons/." \
                        "$out/share/hyproled/icons/"

                    install -Dm644 \
                        "$src/assets/waybar-icons.css" \
                        "$out/share/hyproled/waybar-icons.css"
                    install -Dm644 \
                        "$src/config.schema.json" \
                        "$out/share/hyproled/config.schema.json"

                    install -Dm644 \
                        "$src/config.example.json" \
                        "$out/share/hyproled/config.example.json"

                    install -Dm644 \
                        "$src/README.md" \
                        "$out/share/doc/hyproled/README.md"

                    install -Dm644 \
                        "$src/LICENSE" \
                        "$out/share/licenses/hyproled/LICENSE"

                    wrapProgram "$out/bin/hyproled" \
                        --prefix PATH : \
                        "${pkgs.lib.makeBinPath runtimeInputs}"

                    runHook postInstall
                '';

                meta = {
                    description = "Hyprland shader utility for OLED burn-in mitigation";
                    homepage = "https://github.com/Atiumtea/hyproled";
                    license = pkgs.lib.licenses.bsd3;
                    mainProgram = "hyproled";
                    platforms = systems;
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
                    glslang
                    (pkgs.python3.withPackages (pythonPackages: [ pythonPackages.jsonschema ]))
                ];
            };
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