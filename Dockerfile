# Aucune dépendance tierce : le cœur de simulation, le serveur SSE, le client
# Modbus et les capteurs de terrain sont écrits en bibliothèque standard. Donc
# pas de `pip install`, pas de réseau au build, pas d'API qui bouge sous les
# pieds à la prochaine version mineure.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY sim/ ./sim/
COPY services/ ./services/
COPY scenarios/ ./scenarios/
COPY web/ ./web/
COPY tools/ ./tools/
COPY fixtures/ ./fixtures/

RUN useradd --create-home --uid 10001 cms && chown -R cms /app
USER cms

EXPOSE 8000
CMD ["python3", "services/server.py"]
