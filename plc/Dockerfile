# OpenPLC v3 construit depuis les sources. Il n'existe pas d'image officielle
# stable sur laquelle s'appuyer, et épingler une image communautaire au hasard
# reviendrait à faire confiance à un binaire qu'on n'a pas lu.
#
# Ce build a besoin du réseau. S'il échoue, la stack par défaut fonctionne
# quand même : le pont Modbus dégrade et l'IPMS repasse en modèle logiciel.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential automake libtool make cmake pkg-config \
        python3 python3-pip sudo ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --depth 1 https://github.com/thiagoralves/OpenPLC_v3.git
WORKDIR /opt/OpenPLC_v3
RUN ./install.sh linux

# Le programme de conduite du refroidissement. À charger depuis l'interface
# web (Programs -> Upload) puis à compiler : OpenPLC attend un fichier .st
# compilé par son propre matiec, on ne peut pas le pré-compiler ici.
COPY program.st /opt/OpenPLC_v3/webserver/st_files/ipms.st

EXPOSE 8080 502
CMD ["python3", "webserver/webserver.py"]
