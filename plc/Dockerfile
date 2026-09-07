# OpenPLC v3 construit depuis les sources. Il n'existe pas d'image officielle
# stable sur laquelle s'appuyer, et épingler une image communautaire au hasard
# reviendrait à faire confiance à un binaire qu'on n'a pas lu.
#
# Ce build a besoin du réseau. S'il échoue, la stack par défaut fonctionne
# quand même : le pont Modbus dégrade et l'IPMS repasse en modèle logiciel.
FROM debian:bookworm-slim

# python3-serial via apt, pas pip : sur bookworm, pip3 install refuse
# d'écrire dans l'interpréteur système (PEP 668, « externally-managed-
# environment ») sauf à passer --break-system-packages. install.sh
# d'OpenPLC_v3 installe ses dépendances Python par pip3 sans cette option ;
# l'appel échoue mais le script n'est pas en set -e, donc le build continue
# sans pyserial — d'où ModuleNotFoundError: No module named 'serial' au
# démarrage du serveur web, pas au build. On l'installe donc en paquet
# système, ce qui contourne le problème proprement.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential automake libtool make cmake pkg-config \
        python3 python3-pip python3-serial sudo ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --depth 1 https://github.com/thiagoralves/OpenPLC_v3.git
WORKDIR /opt/OpenPLC_v3
RUN ./install.sh linux
# Filet de sécurité pour le reste des dépendances Python du dépôt (Flask,
# etc.) : si le pip3 interne à install.sh a échoué pour la même raison, on
# le rejoue ici avec l'option qui débloque l'installation système. Ignoré
# sans échec s'il n'y a pas de requirements.txt à cet emplacement.
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt \
        2>/dev/null || true

# Le programme de conduite du refroidissement. À charger depuis l'interface
# web (Programs -> Upload) puis à compiler : OpenPLC attend un fichier .st
# compilé par son propre matiec, on ne peut pas le pré-compiler ici.
COPY program.st /opt/OpenPLC_v3/webserver/st_files/ipms.st

EXPOSE 8080 502
CMD ["python3", "webserver/webserver.py"]
