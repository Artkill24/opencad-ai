FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglu1-mesa \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libsm6 \
    libice6 \
    libxt6 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces esegue il container come UID 1000, non root: senza un
# utente con questo id preciso ogni scrittura in /app (outputs/, la libreria
# di esempi verificati) fallisce con "Permission denied" al primo prompt --
# non al build, quindi il problema si vede solo a deploy pubblicato.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user app/ .

# La cartella viene creata a runtime da main.py, ma la creiamo qui con il
# proprietario giusto: se nascesse a runtime sotto un WORKDIR di root non
# sarebbe scrivibile.
RUN mkdir -p /app/outputs && chown -R user:user /app

RUN mkdir -p /tmp/cache /tmp/matplotlib && chmod 777 /tmp/cache /tmp/matplotlib

USER user

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
