{ pkgs, ... }:

{
  services.runit.hello = {
    enable = true;
    run = ''
      exec ${pkgs.python3}/bin/python -m http.server 8080 \
        --bind 127.0.0.1 --directory /srv/public
    '';
    log.enable = true;
  };
}
