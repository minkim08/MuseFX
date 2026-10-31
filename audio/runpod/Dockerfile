# AudioGen worker for Runpod Serverless. Build context: the repo root.
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/app/hf \
    AUDIOGEN_WEIGHTS=facebook/audiogen-medium \
    AUDIOGEN_MAX_BATCH=12

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY audio/runpod/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --no-deps: audiocraft pins av==11.0.0, which has no Linux wheel and would have
# to be compiled. requirements.txt lists what it needs, with a newer av.
RUN pip install --no-cache-dir --no-deps audiocraft==1.3.0

# Bake AudioGen and its T5 text encoder into the image, so a cold start reads
# them from disk instead of downloading them.
RUN python -c "from audiocraft.models import AudioGen; AudioGen.get_pretrained('facebook/audiogen-medium', device='cpu')"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

COPY audio/baseten/audiogen/model/model.py audio/runpod/handler.py ./
CMD ["python", "-u", "handler.py"]
