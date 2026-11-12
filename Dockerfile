FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed for parsing
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    ffmpeg \
    inotify-tools \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install markitdown
RUN pip install --no-cache-dir "markitdown[all]"
# RUN pip install sentence-transformers scikit-learn

RUN pip install --no-cache-dir \
    torch \
    --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir sentence-transformers

RUN pip install --no-cache-dir scikit-learn


# reportlab — used by scripts/md_to_pdf.py for PDF generation. Confirmed
# via direct container check (docker exec ... python -c 'import reportlab')
# that this is NOT pulled in by markitdown[all] or any other installed
# package — must be installed explicitly.
RUN pip install reportlab

# Install flask for ui on 5000
RUN pip install flask

RUN pip install google-cloud-aiplatform
RUN pip install google-generativeai


# Pin the HuggingFace cache path explicitly so it's guaranteed to match the
# named volume mounted in docker-compose.yml, regardless of how $HOME
# resolves for the container's effective user.
ENV HF_HOME=/root/.cache/huggingface

# Force UTF-8 everywhere. python:3.12-slim often has no UTF-8 locale
# configured, which makes Python's default text encoding (used by
# sys.stdin.read(), open() without encoding=, etc.) fall back to ASCII or
# the container's ambient locale — silently corrupting non-ASCII characters
# (em-dashes, smart quotes) the moment they're read. Setting these env vars
# makes UTF-8 the default for every Python process in the container, so
# individual scripts don't each need to remember to set encoding= everywhere.
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUTF8=1
ENV LANG=C.UTF-8

# Create working dirs
RUN mkdir -p /input /output

EXPOSE 5000

CMD ["bash"]
