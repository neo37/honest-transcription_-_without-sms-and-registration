FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-dev python3-pip \
    ffmpeg curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Re-pin PyTorch to CUDA 12.1 after requirements (whisperx/pyannote may upgrade it to cu128)
RUN pip install --no-cache-dir --force-reinstall \
    torch==2.3.1+cu121 \
    torchaudio==2.3.1+cu121 \
    --index-url https://download.pytorch.org/whl/cu121

COPY manage.py ./
COPY meetrec ./meetrec
COPY recordings ./recordings
COPY wiki_kb ./wiki_kb
COPY mcp_server ./mcp_server
COPY chemico_agent ./chemico_agent

RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "meetrec.wsgi:application"]
